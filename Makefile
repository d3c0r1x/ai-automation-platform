# Короткие команды для повседневной работы.
# Windows: то же самое доступно без make — команды в docs/DEVELOPMENT.md.
.DEFAULT_GOAL := help
PY ?= python

help:  ## Показать список команд
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Установить зависимости (рантайм + проверка) и браузер
	$(PY) -m pip install -r requirements.txt
	$(PY) -m playwright install chromium

run:  ## Поднять API (http://127.0.0.1:8000)
	$(PY) -m uvicorn app.api.main:app --reload --port 8000

worker:  ## Запустить воркер (обрабатывает очередь)
	$(PY) -m app.worker

worker-once:  ## Обработать доступные задачи и выйти
	$(PY) -m app.worker --once

demo:  ## Сквозной прогон без ключей, сети и баз данных
	$(PY) -m app.demo

test:  ## Тесты без pytest
	$(PY) tests/run_all.py

pytest:  ## Тесты через pytest
	$(PY) -m pytest -q

lint:  ## Линтер
	$(PY) -m ruff check app tests scripts

ui:  ## Дашборд в режиме разработки
	cd frontend && npm install && npm run dev

up:  ## Полный контур в Docker: API + воркер + PostgreSQL + Redis
	docker compose up --build

down:  ## Остановить контур
	docker compose down

check: lint test  ## Линтер + тесты
