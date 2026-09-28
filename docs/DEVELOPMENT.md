# Разработка и запуск

## Быстрый старт без ключей и баз

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m app.demo
```

Демо выполняет задачу целиком: план → шаги → таблица → отчёт → запись в SQLite,
и печатает журнал событий. Ключи не нужны: план строит детерминированный
планировщик, товары берутся из встроенного каталога.

```bash
python -m app.demo "Найди 10 товаров категории кофемашина до 30000 ₽, выбери 3 лучших с капучинатором и сохрани csv"
python -m app.demo --json > report.json          # машинный отчёт для CI
python -m app.demo --no-auto-approve             # остановиться на подтверждении
```

## Обычный режим разработки

```bash
# 1. API с автоперезагрузкой
uvicorn app.api.main:app --reload --port 8000

# 2. Воркер — в отдельном терминале (иначе задачи останутся в очереди)
python -m app.worker

# 3. Дашборд
cd frontend && npm install && npm run dev     # http://localhost:5173
```

Без ключа LLM всё работает: `AAP_QUEUE=memory` (значение по умолчанию) держит
очередь внутри процесса API, поэтому даже отдельный воркер не обязателен.
С `AAP_QUEUE=redis` воркер становится обязательным — очередь живёт вне API.

## Полный контур в Docker

```bash
cp .env.example .env
docker compose up --build          # API :8000, PostgreSQL, Redis, воркер
docker compose up -d --scale worker=3   # масштабирование воркеров
```

Дашборд открывается на `http://localhost:8000/` — собранная статика отдаётся тем
же FastAPI-приложением.

## Проверка перед коммитом

```bash
python tests/run_all.py     # без pytest: тот же набор тестов
pytest -q                   # в CI — с отчётностью
ruff check app tests scripts
```

Интеграционная проверка (нужны PostgreSQL и Redis):

```bash
docker compose up -d postgres redis
AAP_DATABASE_URL=postgresql://aap:aap@localhost:5432/aap \
AAP_QUEUE=redis AAP_REDIS_URL=redis://localhost:6379/0 \
python scripts/integration_check.py
```

Она не «проверяет то же самое на другой базе», а отвечает на конкретный вопрос:
задача, положенная в Redis, действительно берётся воркером и доходит до
результата в PostgreSQL.

## HTTP-API

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/tasks` | поставить задачу (`goal`, `source`, `notify`, `auto_approve`) |
| GET | `/api/tasks?limit=&status=` | список задач |
| GET | `/api/tasks/{id}` | задача со шагами и журналом событий |
| POST | `/api/tasks/{id}/approve` | подтвердить ожидающий шаг (`step_id` необязателен) |
| POST | `/api/tasks/{id}/cancel` | отменить задачу (409 для завершённой) |
| POST | `/api/tasks/{id}/retry` | поставить снова (в том числе после ошибки) |
| GET | `/api/tasks/{id}/events?after=0` | события после указанного номера |
| GET | `/api/tasks/{id}/events/stream` | SSE-поток прогресса |
| POST | `/api/webhooks/{source}` | приём извне: JSON `{"goal": …}` или текст в теле |
| GET | `/api/tools` | реестр инструментов: аргументы, подтверждение, теги |
| GET | `/api/stats`, `/health` | счётчики по статусам и состояние сервиса |

Пример без ключей:

```bash
curl -X POST localhost:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Найди 10 товаров категории рюкзак до 5000 ₽, выбери 3 лучших, составь таблицу","auto_approve":true}'

curl localhost:8000/api/tasks/<task_id>
curl -N localhost:8000/api/tasks/<task_id>/events/stream    # живой прогресс
```

## Переменные окружения

Все настройки — с префиксом `AAP_`, полный список с комментариями в
`.env.example`. Ключевые:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `AAP_DATABASE_URL` | `sqlite:///./data/aap.db` | `postgresql://…` для прода |
| `AAP_QUEUE` | `memory` | `redis` для отдельного воркера |
| `AAP_LLM_BASE_URL`, `AAP_LLM_API_KEY`, `AAP_LLM_MODEL` | пусто | без них — детерминированный планировщик |
| `AAP_REQUIRE_APPROVAL` | `1` | подтверждение перед отправкой наружу |
| `AAP_STEP_RETRIES`, `AAP_STEP_TIMEOUT` | `2`, `45` | поведение исполнителя при сбоях |
| `AAP_MAX_STEPS` | `12` | предел шагов в плане |
| `AAP_MARKETPLACE_BASE_URL` | пусто | внешний API поиска товаров |
| `AAP_BROWSER_ENABLED` | `1` | браузерный канал (Playwright) |
| `AAP_SSE_INTERVAL` | `0.5` | период опроса базы в SSE-потоке |

## Данные и артефакты

- `data/aap.db` — база SQLite в локальном режиме (в `.gitignore`);
- `data/artifacts/` — файлы, сохранённые инструментом `save_artifact`
  (CSV-выгрузки и другие результаты);
- имя артефакта обеззараживается: `../../etc/passwd` превращается в
  `etc_passwd.csv`, расширения вне `csv/md/json/txt` отклоняются.

## Как это устроено изнутри

- [ARCHITECTURE.md](ARCHITECTURE.md) — компоненты, путь задачи, состояния;
- [AGENT.md](AGENT.md) — контракт плана, ссылки между шагами, добавление
  инструмента, подключение своей модели;
- [DECISIONS.md](DECISIONS.md) — почему сделано именно так.

## Частые вопросы при разработке

**Задача висит в `queued`.** Воркер не запущен или слушает другую очередь:
проверьте `AAP_QUEUE`/`AAP_REDIS_URL` у API и воркера.

**Задача висит в `waiting_approval`.** Это не ошибка: шаг отправки наружу ждёт
человека. `POST /api/tasks/{id}/approve`.

**План построен кодом, хотя ключ LLM есть.** Смотрите `plan.notes` — там причина
отказа (неизвестный инструмент, плохие аргументы, недоступный провайдер).

**«Источник данных — демонстрационный каталог».** Не задан
`AAP_MARKETPLACE_BASE_URL`; в реальном режиме этот текст в отчёте не появляется.
