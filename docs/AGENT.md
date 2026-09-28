# Агент, инструменты и контракт плана

Документ для того, кто будет добавлять новый инструмент или подключать свою
модель. Здесь описано, что именно агент может и чего не может, и как это
проверяется кодом.

## Что делает агент, а что — нет

| Решает агент | Решает код |
|---|---|
| какие шаги нужны для задачи | арифметика (цена, рейтинг, отзывы, скоринг) |
| в каком порядке их выполнять | формат таблицы, CSV и сообщения |
| какие требования важны в запросе | извлечение чисел и цены из текста |
| нужен ли браузер или хватит API | повторы, таймауты, порядок, состояние |
| как сформулировать отчёт | доступность инструментов и схемы аргументов |

Принцип: **всё, что можно посчитать, считает код; модель выбирает и объясняет.**
Это не недоверие к модели, а разделение ответственности: числа проверяемы, а
«модель так решила» — нет.

## План

```json
{
  "goal": "Найди 20 товаров категории наушники до 5000 ₽, выбери 5 лучших, составь таблицу",
  "source": "llm",
  "notes": "план от модели",
  "steps": [
    {"id": "search_products", "tool": "search_products",
     "args": {"query": "наушники", "limit": 20, "max_price": 5000},
     "reason": "собрать выборку товаров по условиям запроса",
     "depends_on": [], "requires_approval": false, "optional": false},
    {"id": "rank_products", "tool": "rank_products",
     "args": {"items": "$search.items", "top": 5},
     "reason": "отобрать лучшие по цене и отзывам",
     "depends_on": ["search_products"], "requires_approval": false, "optional": false},
    {"id": "build_table", "tool": "build_table",
     "args": {"rows": "$rank.top"},
     "reason": "представить результат таблицей",
     "depends_on": ["rank_products"], "requires_approval": false, "optional": false},
    {"id": "send_report", "tool": "send_report",
     "args": {"message": "Готово: {{search.count}} товаров, отобрано 5",
              "channel": "console", "table_markdown": "$table.markdown"},
     "reason": "доставить отчёт пользователю",
     "depends_on": ["build_table"], "requires_approval": true, "optional": false}
  ]
}
```

Служебные поля шага (`reason`, `depends_on`, `optional`, `id`) модель может
передавать прямо в аргументах — они извлекаются до валидации. Так промпт
остаётся простым: не нужно помнить о двух разных форматах.

## Ссылки между шагами

| Форма | Значение |
|---|---|
| `"$search.items"` | объект целиком (список, словарь, число) |
| `"$rank.top.0.url"` | поле первого элемента списка |
| `"$rank.top\|items\|ranked"` | первое существующее поле из перечисленных |
| `"Найдено {{search.count}} товаров"` | подстановка значения внутрь текста |
| `"{{rank.top.0.price\|money}}"` | подстановка с форматированием (1 990 ₽) |

**Псевдонимы.** Модель не знает наших идентификаторов и пишет ссылки по смыслу:
`$search.items`, `$table.markdown`. Планировщик приводит их к реальным шагам:
имя инструмента и его части (`search_products` → `search`, `products`,
`build_table` → `table`) становятся псевдонимами. Ссылка на то, чего нет в
плане, — ошибка, и она ловится до выполнения.

## Реестр инструментов

| Инструмент | Аргументы | Что возвращает | Подтверждение |
|---|---|---|---|
| `search_products` | `query`, `limit` (1–50), `max_price`, `marketplace` | `items[]`, `count`, `source` (`api`/`demo`) | нет |
| `rank_products` | `items[]`, `top`, `prefer[]`, `weights` | `ranked[]`, `top[]`, `price_range`, `requirements` | нет |
| `open_page` | `url`, `limit` | `text`, `title`, `channel` (`playwright`/`http`/`demo`) | нет |
| `build_table` | `rows[]`, `columns[]`, `title` | `markdown`, `csv`, `rows` | нет |
| `save_artifact` | `name`, `content`, `suffix` | `path`, `name`, `bytes` | нет |
| `send_report` | `message`, `channel`, `target`, `table_markdown` | `delivered`, `channel`, `simulated` | **да** |

Схема каждого инструмента строится из pydantic-модели (`Tool.params`) — одна
модель и для tool calling, и для проверки аргументов. Добавить поле можно в одном
месте, и оно появится и в промпте, и в валидации.

## Добавить свой инструмент

```python
from pydantic import Field
from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams


class PriceHistoryParams(ToolParams):
    item_id: str = Field(description="Идентификатор товара")
    days: int = Field(default=30, ge=1, le=365)


async def price_history(ctx: ToolContext, params: PriceHistoryParams) -> dict:
    if not ctx.settings.marketplace_base_url:
        raise ToolError("история цен доступна только при настроенном API")
    ...  # ctx.settings, ctx.http_timeout, ctx.artifacts_dir
    return {"points": [...], "source": "api"}


PRICE_HISTORY = Tool(
    name="price_history",
    description="История цен товара за период: минимум, максимум, средняя.",
    params=PriceHistoryParams,
    run=price_history,
    tags=("api", "marketplace"),
)
```

Затем — в `build_registry()` (`app/core/tools/base.py`). Инструмент сразу
попадает в схемы для модели, в `/api/tools` и в проверку плана. Тесты на него
пишутся как обычные функции: `Tool` можно вызвать напрямую с `ToolContext`.

Правила, которых стоит держаться:

1. **Возвращайте словарь**, а не объект: он уходит и в ссылки между шагами, и в
   JSON базы.
2. **Не глотайте ошибки.** `ToolError` с внятным текстом → повтор (если он
   осмыслен) → понятная ошибка задачи.
3. **Указывайте источник данных.** `source: "api"` или `"demo"` — это то, что
   отличает демонстрацию от интеграции.
4. **Ничего не отправляйте наружу без `requires_approval=True`.**

## Подключение своей модели

Любой OpenAI-совместимый эндпоинт:

```bash
# OpenAI
AAP_LLM_BASE_URL=https://api.openai.com/v1
AAP_LLM_API_KEY=sk-...
AAP_LLM_MODEL=gpt-4o-mini

# OpenRouter
AAP_LLM_BASE_URL=https://openrouter.ai/api/v1
AAP_LLM_MODEL=anthropic/claude-3.5-sonnet

# Локально через Ollama
AAP_LLM_BASE_URL=http://localhost:11434/v1
AAP_LLM_API_KEY=ollama
AAP_LLM_MODEL=qwen2.5:7b-instruct
```

Проверка: `POST /api/tasks` и посмотреть в дашборде поле «план: построен моделью».
Если модель вернула невалидный план, в `notes` будет причина отказа — это и есть
диагностика промпта.

## Ограничения, о которых стоит знать

- **Планировщик не итеративный.** План строится один раз и выполняется целиком;
  перепланирования «по ходу дела» нет. Для длинных сценариев с ветвлениями
  понадобится цикл с оценкой результата после каждого шага.
- **Число шагов ограничено** (`AAP_MAX_STEPS`, по умолчанию 12) — защита от
  плана, который «никогда не кончается».
- **Промпт не содержит примеров задач.** Модель опирается на описания
  инструментов и правила; few-shot примеры улучшили бы качество планов на
  редких сценариях.
- **Guardrails на содержимое страниц не реализованы.** Текст, полученный
  инструментом `open_page`, попадает в отчёт и в ссылки; защита от prompt
  injection внутри страниц — следующий шаг (см. README, «Ограничения»).
