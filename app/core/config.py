"""Настройки платформы.

Все значения читаются из переменных окружения с префиксом `AAP_`. Никаких
секретов в коде: в `.env.example` лежат только имена переменных.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(f"AAP_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ── Хранилище ────────────────────────────────────────────────────────────
    # sqlite:///./data/aap.db — локально и в тестах;
    # postgresql://user:pass@postgres:5432/aap — прод (docker-compose).
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./data/aap.db"))

    # ── Очередь ──────────────────────────────────────────────────────────────
    # memory — в процессе (демо и тесты); redis — отдельный воркер.
    queue: str = field(default_factory=lambda: _env("QUEUE", "memory"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    queue_name: str = field(default_factory=lambda: _env("QUEUE_NAME", "aap:tasks"))

    # ── LLM ──────────────────────────────────────────────────────────────────
    # Любой OpenAI-совместимый эндпоинт: OpenAI, OpenRouter, локальная Ollama.
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    llm_timeout: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT", 60))

    # ── Инструменты ──────────────────────────────────────────────────────────
    marketplace_base_url: str = field(default_factory=lambda: _env("MARKETPLACE_BASE_URL"))
    marketplace_api_key: str = field(default_factory=lambda: _env("MARKETPLACE_API_KEY"))
    browser_enabled: bool = field(default_factory=lambda: _env("BROWSER_ENABLED", "1") == "1")
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # ── Поведение исполнителя ────────────────────────────────────────────────
    step_retries: int = field(default_factory=lambda: _env_int("STEP_RETRIES", 2))
    step_timeout: int = field(default_factory=lambda: _env_int("STEP_TIMEOUT", 45))
    max_steps: int = field(default_factory=lambda: _env_int("MAX_STEPS", 12))
    # Отправка наружу (сообщение, письмо, вебхук) — точка невозврата,
    # поэтому по умолчанию такие шаги ждут подтверждения человека.
    require_approval: bool = field(default_factory=lambda: _env("REQUIRE_APPROVAL", "1") == "1")

    @property
    def demo_mode(self) -> bool:
        """Без ключа LLM платформа работает на детерминированном планировщике."""
        return not (self.llm_base_url and self.llm_api_key)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))


def load_settings() -> Settings:
    return Settings()
