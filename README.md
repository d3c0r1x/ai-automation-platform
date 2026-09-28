# AI Automation Platform

> **Интересный личный проект, над которым я работал длительное время.** Я строил его как исследовательский MVP платформы, в которой естественно-языковая задача превращается в проверяемый план, ставится в очередь, исполняется набором типизированных инструментов и возвращает структурированный результат.
>
> **Status:** active portfolio project / MVP.

[![CI](https://github.com/d3c0r1x/ai-automation-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/d3c0r1x/ai-automation-platform/actions/workflows/ci.yml)

## Идея

Пример задачи:

> Найди 20 товаров дешевле 5000 ₽, сравни их, выбери 5 лучших и подготовь таблицу.

Система превращает её в:

```
natural language
      ↓
planner
      ↓
validated plan
      ↓
queue
      ↓
worker
      ├─ API tools
      ├─ browser tools
      └─ deterministic calculations
      ↓
approval gate
      ↓
report
```

Ключевая идея: **план — это данные**, а не скрытая последовательность вызовов LLM. Его можно проверить, сохранить, поставить на паузу и продолжить.

## Что реализовано

- FastAPI API;
- typed tool contracts на Pydantic;
- validation плана до запуска;
- memory и Redis queue;
- отдельный worker;
- SQLite и PostgreSQL;
- retries, timeouts, checkpoints;
- SSE stream прогресса;
- Playwright browser tool;
- human approval перед внешним / потенциально необратимым действием;
- React/TypeScript dashboard;
- Docker Compose;
- GitHub Actions;
- 90+ deterministic/integration checks.

## Архитектура

```
Web / REST / Webhook
        ↓
      FastAPI
        ↓
 Queue (memory / Redis)
        ↓
     Worker
        ↓
Planner → validation → executor
        ↓
tools / browser / calculations
        ↓
events + persisted task state
        ↓
dashboard / report
```

## Структура

```
app/
  api/                 # HTTP API
  core/
    agent/             # planner, plan model, executor
    tools/             # инструменты и их контракты
  infra/               # DB/queue/infrastructure
  services/            # прикладные сервисы
  worker.py            # фоновые задачи
  demo.py              # deterministic demo

frontend/              # React dashboard

docs/
  DEVELOPMENT.md       # запуск и API
  ARCHITECTURE.md      # устройство системы
  AGENT.md             # контракт агента и tools
  DECISIONS.md         # инженерные решения

tests/                 # unit + integration checks
scripts/               # operational/integration helpers
```

## Быстрый запуск без LLM

Это основной способ быстро посмотреть проект.

### Linux/macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.demo
```

### Windows

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.demo
```

Пример:

```bash
python -m app.demo "Найди 10 товаров категории кофемашина до 30000 ₽, выбери 3 лучших с капучинатором и сохрани csv"
```

JSON-режим:

```bash
python -m app.demo --json > report.json
```

Проверка approval gate:

```bash
python -m app.demo --no-auto-approve
```

Ключи и внешние сервисы для этих сценариев не нужны.

## Полный режим

### API

```bash
uvicorn app.api.main:app --reload --port 8000
```

### Worker

В отдельном терминале:

```bash
python -m app.worker
```

### Dashboard

```bash
cd frontend
npm install
npm run dev
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Поднимаются:

- FastAPI;
- worker;
- PostgreSQL;
- Redis.

После запуска:

- API — `http://localhost:8000`;
- dashboard — `http://localhost:8000/`.

Можно масштабировать worker:

```bash
docker compose up -d --scale worker=3
```

## API

### Создать задачу

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"goal":"Найди 10 товаров категории рюкзак до 5000 ₽, выбери 3 лучших, составь таблицу","auto_approve":true}'
```

### Получить задачу

```bash
curl http://localhost:8000/api/tasks/<task_id>
```

### Поток событий

```bash
curl -N http://localhost:8000/api/tasks/<task_id>/events/stream
```

### Управление

```
POST /api/tasks/{id}/approve
POST /api/tasks/{id}/cancel
POST /api/tasks/{id}/retry
GET  /api/tasks/{id}/events
GET  /api/tools
GET  /api/stats
GET  /health
```

Полная таблица API находится в [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Конфигурация

Все настройки начинаются с `AAP_`.

Основные:

| Переменная | Назначение |
|---|---|
| `AAP_DATABASE_URL` | SQLite/PostgreSQL |
| `AAP_QUEUE` | `memory` или `redis` |
| `AAP_REDIS_URL` | Redis |
| `AAP_LLM_BASE_URL` | OpenAI-compatible endpoint |
| `AAP_LLM_API_KEY` | ключ модели |
| `AAP_LLM_MODEL` | модель |
| `AAP_REQUIRE_APPROVAL` | approval gate |
| `AAP_STEP_RETRIES` | число повторов |
| `AAP_STEP_TIMEOUT` | timeout шага |
| `AAP_MAX_STEPS` | максимальное количество шагов |
| `AAP_BROWSER_ENABLED` | Playwright |
| `AAP_MARKETPLACE_BASE_URL` | внешний источник данных |

Полный список с комментариями — [.env.example](.env.example).

## Почему здесь не всё отдаётся LLM

Числа, ограничения, состояния задач и запись артефактов должны оставаться детерминированными.

Поэтому LLM отвечает за планирование, а:

- schema validation;
- limits;
- arithmetic;
- retries;
- persistence;
- approval;
- side effects

контролируются обычным кодом.

## Approval gate

Для операций, которые выходят за пределы чтения данных, система может перейти в:

```
waiting_approval
```

После этого внешний эффект выполняется только после:

```http
POST /api/tasks/{id}/approve
```

Это позволяет строить automation flows, в которых AI предлагает действие, но финальное решение остаётся за человеком.

## Проверки

```bash
python tests/run_all.py
pytest -q
ruff check app tests scripts
```

Интеграционный контур с PostgreSQL + Redis:

```bash
docker compose up -d postgres redis

AAP_DATABASE_URL=postgresql://aap:aap@localhost:5432/aap \
AAP_QUEUE=redis \
AAP_REDIS_URL=redis://localhost:6379/0 \
python scripts/integration_check.py
```

## Документация для разработчика

- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — пошаговый запуск, HTTP API, env и troubleshooting;
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — путь задачи и состояния;
- [AGENT.md](docs/AGENT.md) — как добавить свой инструмент;
- [DECISIONS.md](docs/DECISIONS.md) — почему выбраны конкретные решения.

## Ограничения

Это MVP, а не production autonomous agent.

Наиболее важные ограничения:

- planner специально консервативный;
- browser content пока нельзя считать полностью защищённым от prompt injection;
- dashboard покрыт тестами слабее backend;
- интеграции требуют адаптации под реальные внешние API.

## AI-assisted development

AI использовался для реализации рутинных модулей, генерации черновиков и тестовых идей.

Я отвечал за декомпозицию задачи, архитектуру, контракты инструментов, интеграции, отладку, проверку и итоговое поведение.

## Лицензия

MIT.
