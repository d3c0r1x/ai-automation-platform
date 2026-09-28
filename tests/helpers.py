"""Общие помощники тестов: временные контексты, фейковые инструменты, скипы.

Тесты запускаются двумя способами:

- `pytest -q` в CI и локально, если pytest установлен;
- `python tests/run_all.py` — тем же набором функций, но без pytest (для
  окружений, где разрешено ставить только рантайм-зависимости).

Поэтому тесты не используют фикстуры и параметризацию pytest: только обычные
функции с `assert` и (при необходимости) `async def`.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # чтобы `python tests/run_all.py` работал из любой папки
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.tools.base import Tool, ToolContext, ToolParams  # noqa: E402


def require(module: str) -> Any:
    """Импортировать модуль или пометить тест пропущенным (честный skip)."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise unittest.SkipTest(f"нет зависимости {module}: {exc}") from exc


def settings(**overrides: Any) -> Settings:
    base = dict(
        database_url="sqlite:///" + str(ROOT / "data" / "test.db"),
        queue="memory",
        llm_base_url="",
        llm_api_key="",
        require_approval=True,
        step_retries=1,
        step_timeout=5,
        max_steps=12,
    )
    base.update(overrides)
    return Settings(**base)


def context(tmp_path: Path | None = None, demo: bool = True, **overrides: Any) -> ToolContext:
    artifacts = Path(tmp_path or (ROOT / "data" / "test-artifacts"))
    artifacts.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        artifacts_dir=artifacts,
        http_timeout=5,
        demo_mode=demo,
        settings=settings(**overrides),
    )


class FailParams(ToolParams):
    label: str = "flaky"


def flaky_tool(name: str = "flaky", fail_times: int = 1, optional_output: bool = True) -> tuple[Tool, dict[str, int]]:
    """Инструмент, который падает `fail_times` раз, а затем работает.

    Нужен, чтобы проверить повторы: успех со второй попытки — это `attempts=2`,
    а не «повезло».
    """
    state = {"calls": 0, "fail_times": fail_times}

    async def run(ctx: ToolContext, params: FailParams) -> dict[str, Any]:
        state["calls"] += 1
        if state["calls"] <= state["fail_times"]:
            raise RuntimeError(f"временный сбой {state['calls']}")
        output: dict[str, Any] = {"ok": True, "label": params.label, "calls": state["calls"]}
        if optional_output:
            output["items"] = [{"id": "x", "price": 100}]
        return output

    return Tool(name=name, description="сломанный инструмент для тестов", params=FailParams, run=run), state


def hanging_tool(name: str = "hanging") -> Tool:
    import asyncio

    async def run(ctx: ToolContext, params: FailParams) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    return Tool(name=name, description="зависающий инструмент", params=FailParams, run=run)
