# ── 1. Сборка дашборда ──────────────────────────────────────────────────────
FROM node:22-alpine AS dashboard
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── 2. Рантайм ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Браузер для инструмента open_page. Обратите внимание: тяжёлый Chromium
# ставится один раз в образ, а не при каждом запуске задачи.
RUN playwright install --with-deps chromium

COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY pyproject.toml ./

# Собранный дашборд отдаёт тот же FastAPI (StaticFiles, если папка существует)
COPY --from=dashboard /ui/dist ./frontend/dist

# Контейнер работает не от root: сервису не нужны права на запись вне /app/data
RUN useradd --create-home --uid 10001 aap \
    && mkdir -p /app/data \
    && chown -R aap:aap /app
USER aap

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:8000/health').raise_for_status()"

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
