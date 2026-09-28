"""Сервис задач: единая точка, через которую работают API, воркер и демо-CLI.

Порядок работы над задачей:

1. `create()` — задача записана в базу со статусом `queued`, поставлена в очередь;
2. `process()` — воркер берёт её, строит план (если плана ещё нет) и исполняет;
3. `resume()` — продолжение после подтверждения человеком;
4. `cancel()` — статус `cancelled`; исполняющий воркер увидит это по базе и
   остановится (поэтому отмена работает и из другого процесса).

Все переходы статусов проходят через `state.ensure_transition`, а результаты
каждого шага сразу пишутся в базу: перезапуск воркера не теряет работу.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.core.agent.executor import ExecutionOutcome, Executor
from app.core.agent.llm import build_llm_client
from app.core.agent.planner import Planner
from app.core.config import Settings, load_settings
from app.core.models import (
    EventKind,
    StepResult,
    Task,
    TaskEvent,
    TaskRequest,
    TaskStatus,
    TaskView,
)
from app.core.state import ensure_transition
from app.core.tools.base import ToolContext, ToolRegistry, build_registry
from app.infra.db import Repository, new_task_id
from app.infra.queue import Queue


class TaskService:
    def __init__(
        self,
        repository: Repository,
        queue: Queue | None = None,
        settings: Settings | None = None,
        registry: ToolRegistry | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.repository = repository
        self.queue = queue
        self.registry = registry or build_registry()
        self.planner = planner or Planner(
            self.registry,
            llm=build_llm_client(self.settings),
            max_steps=self.settings.max_steps,
        )
        self._connected = False

    async def start(self) -> "TaskService":
        await self.repository.connect()
        self._connected = True
        return self

    async def stop(self) -> None:
        if self.queue:
            await self.queue.close()
        await self.repository.close()
        self._connected = False

    # ── Окружение инструментов ──────────────────────────────────────────────
    def tool_context(self) -> ToolContext:
        return ToolContext(
            artifacts_dir=Path("data/artifacts"),
            http_timeout=self.settings.step_timeout,
            demo_mode=self.settings.demo_mode,
            settings=self.settings,
        )

    # ── Публичные операции ──────────────────────────────────────────────────
    async def create(self, request: TaskRequest) -> Task:
        task = Task(
            id=new_task_id(),
            goal=request.goal.strip(),
            source=request.source,
            notify=request.notify,
            auto_approve=request.auto_approve,
        )
        await self.repository.create_task(task)
        await self.repository.add_event(
            TaskEvent(
                task_id=task.id,
                kind=EventKind.TASK_CREATED,
                message=f"задача принята из «{task.source}»",
                payload={"goal": task.goal},
            )
        )
        if self.queue:
            await self.queue.push(task.id)
        return task

    async def process(self, task_id: str) -> ExecutionOutcome:
        task = await self.repository.get_task(task_id)
        if task is None:
            raise KeyError(f"задача {task_id} не найдена")
        if task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
            return ExecutionOutcome(status=task.status, error="задача уже завершена")

        if task.plan is None:
            ensure_transition(task.status, TaskStatus.PLANNING)
            await self._set_status(task, TaskStatus.PLANNING)
            plan = await self.planner.plan(task.goal)
            await self.repository.update_task(task_id, plan=plan)
            await self.repository.add_event(
                TaskEvent(
                    task_id=task_id,
                    kind=EventKind.PLAN_READY,
                    message=f"план готов, шагов: {len(plan.steps)} (источник: {plan.source.value})",
                    payload={"steps": [s.model_dump() for s in plan.steps], "notes": plan.notes},
                )
            )
            task = task.model_copy(update={"plan": plan, "status": TaskStatus.PLANNING})

        ensure_transition(task.status, TaskStatus.RUNNING)
        await self._set_status(task, TaskStatus.RUNNING)

        completed = await self._completed_steps(task_id)
        approved = await self._approved_steps(task_id)

        executor = Executor(
            self.registry,
            self.tool_context(),
            retries=self.settings.step_retries,
            timeout=self.settings.step_timeout,
            require_approval=bool(self.settings.require_approval)
            and not bool(task.auto_approve),
            on_event=self.repository.add_event,
            on_step_result=lambda step: self.repository.save_step(task_id, step),
            is_cancelled=lambda: self._is_cancelled(task_id),
        )
        outcome = await executor.run(
            task_id, task.plan, completed=completed, approved=approved
        )
        await self._apply_outcome(task_id, outcome)
        return outcome

    async def resume(self, task_id: str) -> ExecutionOutcome:
        task = await self.repository.get_task(task_id)
        if task is None:
            raise KeyError(f"задача {task_id} не найдена")
        if task.status not in (TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED):
            raise ValueError(f"задача в статусе {task.status.value}: продолжать нечего")
        if task.plan is None:
            raise ValueError("у задачи нет плана")
        return await self.process(task_id)

    async def approve(self, task_id: str, step_id: str) -> None:
        await self.repository.add_event(
            TaskEvent(
                task_id=task_id,
                kind=EventKind.APPROVED,
                message=f"человек подтвердил шаг {step_id}",
                payload={"step_id": step_id},
            )
        )

    async def cancel(self, task_id: str) -> Task:
        task = await self.repository.get_task(task_id)
        if task is None:
            raise KeyError(f"задача {task_id} не найдена")
        ensure_transition(task.status, TaskStatus.CANCELLED)
        await self._set_status(task, TaskStatus.CANCELLED)
        await self.repository.add_event(
            TaskEvent(task_id=task_id, kind=EventKind.CANCELLED, message="отмена запрошена пользователем")
        )
        return task.model_copy(update={"status": TaskStatus.CANCELLED})

    async def view(self, task_id: str) -> TaskView | None:
        return await self.repository.task_view(task_id)

    async def list(self, limit: int = 50, status: TaskStatus | None = None) -> list[Task]:
        return await self.repository.list_tasks(limit=limit, status=status)

    # ── Внутреннее ──────────────────────────────────────────────────────────
    async def _set_status(self, task: Task, status: TaskStatus) -> None:
        await self.repository.update_task(task.id, status=status)

    async def _is_cancelled(self, task_id: str) -> bool:
        task = await self.repository.get_task(task_id)
        return bool(task and task.status == TaskStatus.CANCELLED)

    async def _completed_steps(self, task_id: str) -> dict[str, StepResult]:
        steps = await self.repository.list_steps(task_id)
        return {s.step_id: s for s in steps if s.ok}

    async def _approved_steps(self, task_id: str) -> set[str]:
        events = await self.repository.list_events(task_id, limit=2000)
        return {
            str(e.payload.get("step_id"))
            for e in events
            if e.kind == EventKind.APPROVED and e.payload.get("step_id")
        }

    async def _apply_outcome(self, task_id: str, outcome: ExecutionOutcome) -> None:
        if outcome.status == TaskStatus.DONE:
            await self.repository.update_task(
                task_id, status=TaskStatus.DONE, result=outcome.result
            )
            await self.repository.add_event(
                TaskEvent(
                    task_id=task_id,
                    kind=EventKind.TASK_FINISHED,
                    message="задача выполнена",
                    payload={"rows": len(outcome.result.rows) if outcome.result else 0},
                )
            )
        elif outcome.status == TaskStatus.WAITING_APPROVAL:
            await self.repository.update_task(task_id, status=TaskStatus.WAITING_APPROVAL)
        elif outcome.status == TaskStatus.CANCELLED:
            await self.repository.update_task(task_id, status=TaskStatus.CANCELLED)
        else:
            await self.repository.update_task(
                task_id, status=TaskStatus.FAILED, error=outcome.error or "неизвестная ошибка"
            )
            await self.repository.add_event(
                TaskEvent(
                    task_id=task_id,
                    kind=EventKind.TASK_FINISHED,
                    message=f"задача завершилась ошибкой: {outcome.error}",
                )
            )

    # ── Цикл воркера ────────────────────────────────────────────────────────
    async def work_loop(self, poll_timeout: float = 1.0, max_tasks: int | None = None) -> int:
        """Обработать задачи из очереди. `max_tasks` — для тестов и `--once`."""
        if self.queue is None:
            raise RuntimeError("воркеру нужна очередь")
        done = 0
        while max_tasks is None or done < max_tasks:
            task_id = await self.queue.pop(timeout=poll_timeout)
            if not task_id:
                if max_tasks is not None:
                    break
                await asyncio.sleep(0.05)
                continue
            try:
                await self.process(task_id)
            except Exception as exc:  # воркер не должен умирать от одной задачи
                await self.repository.update_task(
                    task_id, status=TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
                )
            done += 1
        return done

    async def due_waiting(self) -> list[str]:
        """Задачи, которые ждут подтверждения — для дашборда."""
        tasks = await self.repository.list_tasks(limit=100, status=TaskStatus.WAITING_APPROVAL)
        return [t.id for t in tasks]


def now() -> datetime:
    return datetime.now(timezone.utc)


async def build_service(settings: Settings | None = None, with_queue: bool = True) -> TaskService:
    """Собрать сервис так, как это делает API и воркер."""
    settings = settings or load_settings()
    repository = Repository(settings.database_url)
    queue: Queue | None = None
    if with_queue:
        from app.infra.queue import build_queue

        queue = await build_queue(settings)
    service = TaskService(repository, queue=queue, settings=settings)
    await service.start()
    return service


__all__ = ["TaskService", "build_service"]
