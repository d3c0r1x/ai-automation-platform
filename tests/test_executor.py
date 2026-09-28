"""Исполнитель: повторы, подтверждение, отмена, продолжение после сбоя."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.agent.executor import Executor
from app.core.agent.planner import Planner
from app.core.models import EventKind, Plan, PlanStep, StepResult, TaskEvent, TaskStatus
from app.core.tools.base import ToolRegistry, build_registry
from tests.helpers import context, flaky_tool, hanging_tool


def _collect() -> tuple[list[TaskEvent], Any]:
    events: list[TaskEvent] = []

    async def hook(event: TaskEvent) -> None:
        events.append(event)

    return events, hook


def _executor(registry: ToolRegistry, **kwargs: Any) -> tuple[Executor, list[TaskEvent]]:
    events, hook = _collect()
    options = dict(
        retries=1,
        timeout=5,
        backoff=0,
        require_approval=True,
        on_event=hook,
    )
    options.update(kwargs)
    return Executor(registry, context(), **options), events


def _plan(*steps: PlanStep) -> Plan:
    return Plan(goal="тест", steps=list(steps))


def test_happy_path_runs_real_tools_end_to_end() -> None:
    registry = build_registry()
    plan = asyncio.run(Planner(registry).plan("найди 10 товаров категории наушники до 4000 ₽, выбери 3 лучших"))
    executor, events = _executor(registry, require_approval=False)
    outcome = asyncio.run(executor.run("task_test", plan))

    assert outcome.status == TaskStatus.DONE
    assert outcome.result is not None
    assert len(outcome.result.rows) == 3
    assert "Найдено 10 товаров" in outcome.result.summary
    assert "демонстрационный каталог" in outcome.result.summary
    assert outcome.result.delivery["delivered"] is True
    kinds = [e.kind for e in events]
    assert EventKind.STEP_STARTED in kinds and EventKind.STEP_FINISHED in kinds


def test_retry_succeeds_on_second_attempt() -> None:
    tool, state = flaky_tool("flaky", fail_times=1)
    registry = ToolRegistry().register(tool)
    executor, _ = _executor(registry)
    plan = _plan(PlanStep(id="s1", tool="flaky", args={}))
    outcome = asyncio.run(executor.run("task_retry", plan))

    assert outcome.status == TaskStatus.DONE
    assert outcome.steps[0].attempts == 2
    assert state["calls"] == 2


def test_failure_after_all_retries_stops_the_task() -> None:
    tool, state = flaky_tool("flaky", fail_times=99)
    registry = ToolRegistry().register(tool)
    executor, events = _executor(registry, retries=1)
    plan = _plan(PlanStep(id="s1", tool="flaky", args={}))
    outcome = asyncio.run(executor.run("task_fail", plan))

    assert outcome.status == TaskStatus.FAILED
    assert "временный сбой" in (outcome.error or "")
    assert state["calls"] == 2  # первоначальная попытка + один повтор
    assert any(e.kind == EventKind.STEP_FAILED for e in events)


def test_optional_step_failure_does_not_break_the_task() -> None:
    tool, _ = flaky_tool("optional_flaky", fail_times=99)
    registry = build_registry()
    registry.register(tool)
    plan = _plan(
        PlanStep(id="s1", tool="search_products", args={"query": "наушники", "limit": 3}),
        PlanStep(id="s2", tool="optional_flaky", args={}, optional=True),
        PlanStep(id="s3", tool="build_table", args={"rows": "$s1.items"}),
    )
    executor, events = _executor(registry, require_approval=False)
    outcome = asyncio.run(executor.run("task_optional", plan))

    assert outcome.status == TaskStatus.DONE
    assert outcome.result is not None
    assert [s.tool for s in outcome.result.skipped] == ["optional_flaky"]
    assert "пропущены необязательные шаги" in outcome.result.summary
    assert any(e.kind == EventKind.STEP_FINISHED and e.payload.get("optional") for e in events)


def test_approval_gate_pauses_and_resume_finishes() -> None:
    registry = build_registry()
    plan = asyncio.run(Planner(registry).plan("найди 5 товаров категории рюкзак, составь таблицу и отправь отчёт"))
    executor, events = _executor(registry, require_approval=True)

    paused = asyncio.run(executor.run("task_gate", plan))
    assert paused.status == TaskStatus.WAITING_APPROVAL
    assert paused.waiting_step == "report"
    assert any(e.kind == EventKind.APPROVAL_REQUIRED for e in events)
    assert paused.result is None  # без подтверждения результата нет

    done = asyncio.run(
        executor.run(
            "task_gate",
            plan,
            completed={s.step_id: s for s in paused.steps if s.ok},
            approved={"report"},
        )
    )
    assert done.status == TaskStatus.DONE
    assert done.result is not None and done.result.delivery["delivered"] is True


def test_completed_steps_are_not_executed_twice() -> None:
    """Контрольная точка: после перезапуска воркер не повторяет готовые шаги."""
    calls = {"n": 0}

    async def run(ctx: Any, params: Any) -> dict[str, Any]:
        calls["n"] += 1
        return {"value": calls["n"]}

    from app.core.tools.base import Tool, ToolParams

    class P(ToolParams):
        pass

    registry = ToolRegistry()
    registry.register(Tool(name="counter", description="счётчик", params=P, run=run))
    plan = _plan(PlanStep(id="s1", tool="counter", args={}), PlanStep(id="s2", tool="counter", args={}))
    executor, _ = _executor(registry)

    first = asyncio.run(executor.run("task_ckpt", plan))
    assert calls["n"] == 2 and first.status == TaskStatus.DONE

    second = asyncio.run(
        executor.run("task_ckpt", plan, completed={s.step_id: s for s in first.steps})
    )
    assert second.status == TaskStatus.DONE
    assert calls["n"] == 2  # повторного вызова не было


def test_cancellation_stops_before_next_step() -> None:
    registry = build_registry()
    plan = asyncio.run(Planner(registry).plan("найди 3 товара категории рюкзак"))

    async def cancelled() -> bool:
        return True

    executor, events = _executor(registry, is_cancelled=cancelled)
    outcome = asyncio.run(executor.run("task_cancel", plan))
    assert outcome.status == TaskStatus.CANCELLED
    assert any(e.kind == EventKind.CANCELLED for e in events)


def test_reference_to_step_missing_from_plan_is_rejected_before_running() -> None:
    registry = build_registry()
    plan = _plan(PlanStep(id="s1", tool="build_table", args={"rows": "$nothing.items"}))
    executor, _ = _executor(registry)
    outcome = asyncio.run(executor.run("task_ref", plan))
    assert outcome.status == TaskStatus.FAILED
    assert "nothing" in (outcome.error or "") and "нет в плане" in (outcome.error or "")


def test_reference_to_failed_optional_step_fails_the_dependent_step() -> None:
    """Ссылка на результат шага, который не выполнился, — это ошибка, а не пустая таблица."""
    tool, _ = flaky_tool("optional_flaky", fail_times=99)
    registry = build_registry()
    registry.register(tool)
    plan = _plan(
        PlanStep(id="s1", tool="optional_flaky", args={}, optional=True),
        PlanStep(id="s2", tool="build_table", args={"rows": "$s1.items"}),
    )
    executor, _ = _executor(registry, require_approval=False)
    outcome = asyncio.run(executor.run("task_ref2", plan))
    assert outcome.status == TaskStatus.FAILED
    assert "s2" in (outcome.error or "") and "нет результатов шага" in (outcome.error or "")


def test_plan_cycle_is_rejected() -> None:
    registry = build_registry()
    plan = _plan(
        PlanStep(id="a", tool="build_table", args={"rows": "$b.items"}),
        PlanStep(id="b", tool="build_table", args={"rows": "$a.items"}),
    )
    executor, _ = _executor(registry)
    outcome = asyncio.run(executor.run("task_cycle", plan))
    assert outcome.status == TaskStatus.FAILED
    assert "цикл" in (outcome.error or "")


def test_step_timeout_is_enforced() -> None:
    registry = ToolRegistry().register(hanging_tool("hanging"))
    executor, _ = _executor(registry, retries=0, timeout=1)
    plan = _plan(PlanStep(id="s1", tool="hanging", args={}))
    outcome = asyncio.run(executor.run("task_timeout", plan))
    assert outcome.status == TaskStatus.FAILED
    assert "Timeout" in (outcome.error or "")


def test_result_records_demo_source_explicitly() -> None:
    registry = build_registry()
    plan = asyncio.run(Planner(registry).plan("найди 4 товара категории пылесос, выбери 2"))
    executor, _ = _executor(registry, require_approval=False)
    outcome = asyncio.run(executor.run("task_demo", plan))
    assert outcome.result is not None
    assert outcome.result.data["search"]["source"] == "demo"
    assert outcome.result.data["plan_source"] == "deterministic"


def test_steps_are_reported_in_execution_order() -> None:
    registry = build_registry()
    plan = asyncio.run(Planner(registry).plan("найди 6 товаров категории рюкзак, выбери 2, сохрани csv"))
    executor, _ = _executor(registry, require_approval=False)
    outcome = asyncio.run(executor.run("task_order", plan))
    tools = [s.tool for s in outcome.steps]
    assert tools.index("search_products") < tools.index("rank_products") < tools.index("build_table")
    assert "csv" in plan.steps[-2].id or any(s.tool == "save_artifact" for s in plan.steps)
    assert all(isinstance(s, StepResult) for s in outcome.steps)
