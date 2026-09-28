"""Планировщик: детерминированный план, план от LLM и его валидация."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.agent.llm import LlmUnavailable, ToolCall
from app.core.agent.planner import Planner
from app.core.agent.refs import ordered_steps, resolve_args
from app.core.models import PlanSource
from app.core.tools.base import build_registry
from tests.helpers import settings

GOAL = "Найди 20 товаров категории наушники до 5000 ₽, выбери 5 лучших, составь таблицу и отправь мне отчёт"


class FakeLlm:
    """Фейковая модель: возвращает заранее заданные вызовы или падает."""

    def __init__(self, calls: list[ToolCall] | None = None, error: Exception | None = None) -> None:
        self.calls = calls or []
        self.error = error
        self.seen_tools: list[str] = []

    @property
    def available(self) -> bool:
        return True

    async def plan(self, goal: str, tools: list[dict[str, Any]], max_steps: int) -> tuple[list[ToolCall], str]:
        self.seen_tools = [t["function"]["name"] for t in tools]
        if self.error:
            raise self.error
        return self.calls, "план от тестовой модели"

    async def summarize(self, goal: str, facts: dict[str, Any]) -> str:  # pragma: no cover
        return "сводка"


def test_deterministic_plan_covers_the_example_goal() -> None:
    planner = Planner(build_registry(), max_steps=12)
    plan = asyncio.run(planner.plan(GOAL))
    assert plan.source == PlanSource.DETERMINISTIC
    assert [s.tool for s in plan.steps] == ["search_products", "rank_products", "build_table", "send_report"]
    assert plan.steps[0].args["limit"] == 20
    assert plan.steps[0].args["max_price"] == 5000
    assert plan.steps[1].args["top"] == 5
    assert plan.steps[-1].requires_approval is True


def test_deterministic_plan_steps_are_executable_in_order() -> None:
    """Каждая ссылка плана должна разрешаться результатами предыдущих шагов."""
    planner = Planner(build_registry(), max_steps=12)
    plan = asyncio.run(planner.plan(GOAL + " и сохрани csv"))
    outputs: dict[str, Any] = {}
    fake_outputs = {
        "search_products": {"items": [{"title": "A", "price": 100, "url": "http://x", "marketplace": "ozon"}], "count": 1},
        "rank_products": {"top": [{"title": "A", "price": 100, "url": "http://x", "marketplace": "ozon"}], "requirements": []},
        "build_table": {"markdown": "| a |", "csv": "a\n", "rows": []},
        "save_artifact": {"path": "/tmp/x", "name": "x.csv"},
        "send_report": {"delivered": True},
    }
    for step in ordered_steps(plan.steps):
        args = resolve_args(step.args, outputs)  # не должно бросить RefError
        assert args is not None
        outputs[step.id] = fake_outputs[step.tool]


def test_llm_plan_is_used_when_valid() -> None:
    calls = [
        ToolCall("search_products", {"query": "наушники", "limit": 20, "max_price": 5000}),
        ToolCall("rank_products", {"items": "$search.items", "top": 5}),
        ToolCall("build_table", {"rows": "$rank.top"}),
        ToolCall("send_report", {"message": "готово", "table_markdown": "$table.markdown"}),
    ]
    # Ссылки написаны «по смыслу», а не по внутренним id — это штатный случай.
    assert "$search.items" in str(calls[1].arguments)
    planner = Planner(build_registry(), llm=FakeLlm(calls), max_steps=12)
    plan = asyncio.run(planner.plan(GOAL))
    assert plan.source == PlanSource.LLM
    assert [s.tool for s in plan.steps] == ["search_products", "rank_products", "build_table", "send_report"]
    # Идентификатор шага — имя инструмента: так ссылки модели ($search.items)
    # совпадают с планом и читаются в дашборде без расшифровки.
    assert [s.id for s in plan.steps] == ["search_products", "rank_products", "build_table", "send_report"]
    assert plan.notes == "план от тестовой модели"


def test_llm_receives_tool_schemas() -> None:
    llm = FakeLlm([ToolCall("search_products", {"query": "x"})])
    planner = Planner(build_registry(), llm=llm, max_steps=12)
    asyncio.run(planner.plan(GOAL))
    assert "search_products" in llm.seen_tools and "send_report" in llm.seen_tools


def test_unknown_tool_falls_back_to_deterministic() -> None:
    llm = FakeLlm([ToolCall("hack_the_planet", {"x": 1})])
    plan = asyncio.run(Planner(build_registry(), llm=llm).plan(GOAL))
    assert plan.source == PlanSource.DETERMINISTIC
    assert "отклонён" in plan.notes


def test_invalid_arguments_fall_back() -> None:
    llm = FakeLlm([ToolCall("search_products", {"query": "x", "limit": "много"})])
    plan = asyncio.run(Planner(build_registry(), llm=llm).plan(GOAL))
    assert plan.source == PlanSource.DETERMINISTIC
    assert "валидацию" in plan.notes


def test_bad_reference_falls_back() -> None:
    calls = [
        ToolCall("search_products", {"query": "x"}),
        ToolCall("build_table", {"rows": "$missing_step.items"}),
    ]
    plan = asyncio.run(Planner(build_registry(), llm=FakeLlm(calls)).plan(GOAL))
    assert plan.source == PlanSource.DETERMINISTIC


def test_llm_unavailable_falls_back() -> None:
    llm = FakeLlm(error=LlmUnavailable("провайдер ответил 503"))
    plan = asyncio.run(Planner(build_registry(), llm=llm).plan(GOAL))
    assert plan.source == PlanSource.DETERMINISTIC
    assert "503" in plan.notes


def test_llm_plan_without_delivery_gets_table_and_report_appended() -> None:
    """Модель остановилась на поиске — платформа обязана довести до результата."""
    calls = [ToolCall("search_products", {"query": "наушники", "limit": 5})]
    planner = Planner(build_registry(), llm=FakeLlm(calls))
    plan = asyncio.run(planner.plan(GOAL))
    tools = [s.tool for s in plan.steps]
    assert tools[-2:] == ["build_table", "send_report"]
    assert plan.steps[-1].depends_on == ["search_products_table"]


def test_max_steps_is_enforced() -> None:
    calls = [ToolCall("search_products", {"query": f"q{i}"}) for i in range(20)]
    planner = Planner(build_registry(), llm=FakeLlm(calls), max_steps=3)
    plan = asyncio.run(planner.plan(GOAL))
    assert len(plan.steps) <= 5  # 3 от модели + достроенные таблица и отправка


def test_reserved_keys_from_model_are_understood() -> None:
    calls = [
        ToolCall(
            "search_products",
            {
                "query": "наушники",
                "limit": 4,
                "reason": "нужно собрать выборку",
                "depends_on": [],
                "optional": False,
                "id": "self",
            },
        )
    ]
    plan = asyncio.run(Planner(build_registry(), llm=FakeLlm(calls)).plan(GOAL))
    assert plan.steps[0].reason == "нужно собрать выборку"


def test_planner_without_configured_llm_uses_code() -> None:
    assert settings().demo_mode is True
    plan = asyncio.run(Planner(build_registry()).plan("найди 3 товара категории рюкзак"))
    assert plan.source == PlanSource.DETERMINISTIC
    assert plan.steps[0].args["limit"] == 3
