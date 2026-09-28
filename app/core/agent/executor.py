"""Исполнитель плана.

Что здесь важно и почему так:

- **Повторы с ростом паузы.** Внешние API отваливаются: 502, таймаут, обрыв.
  Один и тот же шаг повторяется `retries` раз, и только после этого задача
  падает. Ошибка конфигурации (ссылка на несуществующий шаг) не повторяется
  никогда — от повторов она не станет лучше.
- **Таймаут на шаг.** Задача не должна висеть вечно из-за одного зависшего
  запроса; по умолчанию 45 секунд.
- **Контрольная точка после каждого шага.** Результат пишется сразу, поэтому
  упавший воркер продолжает задачу с того же места, а не с начала.
- **Подтверждение человека перед отправкой наружу.** Шаг с
  `requires_approval` останавливает выполнение: задача уходит в
  `waiting_approval` и продолжается отдельным вызовом `resume()`.
- **Необязательные шаги не роняют задачу.** Если не удалось открыть страницу
  товара — это видно в результате (`skipped`), но таблица и отчёт всё равно
  приходят.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.agent.refs import RefError, ordered_steps, resolve_args
from app.core.models import (
    EventKind,
    Plan,
    SkippedStep,
    StepResult,
    TaskEvent,
    TaskResult,
    TaskStatus,
)
from app.core.tools.base import ToolContext, ToolRegistry

EventHook = Callable[[TaskEvent], Awaitable[None]]
StepHook = Callable[[StepResult], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[bool]]


@dataclass
class ExecutionOutcome:
    status: TaskStatus
    steps: list[StepResult] = field(default_factory=list)
    result: TaskResult | None = None
    error: str | None = None
    waiting_step: str | None = None


class Executor:
    def __init__(
        self,
        registry: ToolRegistry,
        ctx: ToolContext,
        *,
        retries: int = 2,
        timeout: int = 45,
        backoff: float = 0.2,
        require_approval: bool = True,
        on_event: EventHook | None = None,
        on_step_result: StepHook | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> None:
        self._registry = registry
        self._ctx = ctx
        self._retries = max(0, retries)
        self._timeout = timeout
        self._backoff = backoff
        self._require_approval = require_approval
        self._on_event = on_event
        self._on_step_result = on_step_result
        self._is_cancelled = is_cancelled

    async def _emit(self, task_id: str, kind: EventKind, message: str = "", **payload: Any) -> None:
        if not self._on_event:
            return
        await self._on_event(TaskEvent(task_id=task_id, kind=kind, message=message, payload=payload))

    async def _cancelled(self) -> bool:
        return bool(self._is_cancelled and await self._is_cancelled())

    async def _call_step(self, tool_name: str, args: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Вызвать инструмент с повторами. Возвращает (результат, число попыток)."""
        tool = self._registry.get(tool_name)
        params = self._registry.validate_args(tool_name, args)
        last_error: Exception | None = None

        for attempt in range(1, self._retries + 2):
            try:
                output = await asyncio.wait_for(tool.run(self._ctx, params), timeout=self._timeout)
                return dict(output or {}), attempt
            except asyncio.CancelledError:
                raise
            except RefError:
                raise
            except Exception as exc:  # причину надо сохранить и показать
                last_error = exc
                if attempt <= self._retries:
                    await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))
        raise RuntimeError(f"{type(last_error).__name__}: {last_error}") from last_error

    async def run(
        self,
        task_id: str,
        plan: Plan,
        *,
        completed: dict[str, StepResult] | None = None,
        approved: set[str] | None = None,
    ) -> ExecutionOutcome:
        completed = dict(completed or {})
        approved = set(approved or {})
        outputs: dict[str, Any] = {sid: r.output for sid, r in completed.items() if r.ok}
        resolved_args: dict[str, dict[str, Any]] = {}
        results: list[StepResult] = [completed[sid] for sid in completed]
        skipped: list[SkippedStep] = []

        try:
            steps = ordered_steps(plan.steps)
        except RefError as exc:
            await self._emit(task_id, EventKind.STEP_FAILED, str(exc))
            return ExecutionOutcome(status=TaskStatus.FAILED, steps=results, error=str(exc))

        for step in steps:
            if await self._cancelled():
                await self._emit(task_id, EventKind.CANCELLED, "задача отменена")
                return ExecutionOutcome(status=TaskStatus.CANCELLED, steps=results)

            previous = completed.get(step.id)
            if previous and previous.ok:
                outputs.setdefault(step.id, previous.output)
                continue

            tool = self._registry.get(step.tool)
            needs_approval = (
                self._require_approval
                and (tool.requires_approval or step.requires_approval)
                and step.id not in approved
            )
            if needs_approval:
                await self._emit(
                    task_id,
                    EventKind.APPROVAL_REQUIRED,
                    f"шаг {step.id} ({step.tool}) ждёт подтверждения человека",
                    step_id=step.id,
                    tool=step.tool,
                )
                return ExecutionOutcome(
                    status=TaskStatus.WAITING_APPROVAL,
                    steps=results,
                    waiting_step=step.id,
                    error=None,
                )

            try:
                args = resolve_args(step.args, outputs)
            except RefError as exc:
                message = f"шаг {step.id}: {exc}"
                await self._emit(task_id, EventKind.STEP_FAILED, message, step_id=step.id)
                return ExecutionOutcome(
                    status=TaskStatus.FAILED,
                    steps=results,
                    error=message,
                )

            await self._emit(task_id, EventKind.STEP_STARTED, f"{step.tool}: {step.reason}", step_id=step.id)
            started = time.perf_counter()
            try:
                output, attempts = await self._call_step(step.tool, args)
            except Exception as exc:
                duration = int((time.perf_counter() - started) * 1000)
                failure = StepResult(
                    step_id=step.id,
                    tool=step.tool,
                    ok=False,
                    attempts=self._retries + 1,
                    duration_ms=duration,
                    error=f"{type(exc).__name__}: {exc}",
                )
                results.append(failure)
                if self._on_step_result:
                    await self._on_step_result(failure)
                if step.optional:
                    skipped.append(SkippedStep(step_id=step.id, tool=step.tool, reason=failure.error or ""))
                    await self._emit(
                        task_id,
                        EventKind.STEP_FINISHED,
                        f"необязательный шаг {step.id} пропущен: {failure.error}",
                        step_id=step.id,
                        optional=True,
                    )
                    continue
                await self._emit(task_id, EventKind.STEP_FAILED, failure.error or "шаг упал", step_id=step.id)
                return ExecutionOutcome(status=TaskStatus.FAILED, steps=results, error=failure.error)

            duration = int((time.perf_counter() - started) * 1000)
            result = StepResult(
                step_id=step.id,
                tool=step.tool,
                ok=True,
                attempts=attempts,
                duration_ms=duration,
                output=output,
            )
            results.append(result)
            outputs[step.id] = output
            resolved_args[step.id] = args
            if self._on_step_result:
                await self._on_step_result(result)
            await self._emit(
                task_id,
                EventKind.STEP_FINISHED,
                f"{step.tool} выполнен за {duration} мс",
                step_id=step.id,
                attempts=attempts,
            )

        return ExecutionOutcome(
            status=TaskStatus.DONE,
            steps=results,
            result=self.build_result(plan, results, outputs, resolved_args, skipped),
        )

    # ── Сборка результата ───────────────────────────────────────────────────
    def build_result(
        self,
        plan: Plan,
        results: list[StepResult],
        outputs: dict[str, Any],
        resolved_args: dict[str, dict[str, Any]],
        skipped: list[SkippedStep],
    ) -> TaskResult:
        by_tool = {r.tool: r for r in results if r.ok}
        table = by_tool.get("build_table")
        delivery = by_tool.get("send_report")
        search = by_tool.get("search_products")

        table_markdown = str(table.output.get("markdown", "")) if table else ""
        rows = list(table.output.get("rows", [])) if table else []
        artifacts = {
            str(r.output.get("name")): str(r.output.get("path"))
            for r in results
            if r.tool == "save_artifact" and r.ok
        }

        data: dict[str, Any] = {"plan_source": plan.source.value, "steps": len(results)}
        if search:
            data["search"] = {
                "count": search.output.get("count", 0),
                "source": search.output.get("source", "unknown"),
                "query": search.output.get("query", ""),
            }

        summary = ""
        report_step = next((s for s in plan.steps if s.tool == "send_report"), None)
        if report_step and report_step.id in resolved_args:
            summary = str(resolved_args[report_step.id].get("message", "")).strip()
        if not summary:
            count = (search.output.get("count", 0) if search else 0)
            shown = len(rows)
            summary = f"Собрано {count} товаров, в результат отобрано {shown}."

        notes: list[str] = []
        if search and search.output.get("source") == "demo":
            notes.append("источник данных — демонстрационный каталог, внешние запросы не выполнялись")
        if skipped:
            notes.append(
                "пропущены необязательные шаги: " + ", ".join(f"{s.tool} ({s.reason})" for s in skipped)
            )
        if delivery:
            out = delivery.output
            if out.get("simulated"):
                notes.append(f"доставка в «{out.get('channel')}» сымитирована: {out.get('note', '')}".strip())
            elif not out.get("delivered"):
                notes.append("доставка не подтверждена")
        if notes:
            summary = summary.rstrip() + "\n\n_" + "; ".join(notes) + "_"

        delivery_info = dict(delivery.output) if delivery else {}
        return TaskResult(
            summary=summary,
            table_markdown=table_markdown,
            rows=rows,
            artifacts=artifacts,
            data=data,
            skipped=skipped,
            delivery=delivery_info,
        )
