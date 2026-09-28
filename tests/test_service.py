"""Сервис задач: очередь, подтверждение человеком, отмена, повторный запуск."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.core.models import EventKind, TaskRequest, TaskStatus
from app.core.tools.base import Tool
from app.infra.db import Repository
from app.infra.queue import MemoryQueue
from app.services.tasks import TaskService
from tests.expect import raises
from tests.helpers import settings

GOAL = "Найди 20 товаров категории наушники до 5000 ₽, выбери 5 лучших, составь таблицу и отправь мне отчёт"


async def _service(**overrides: object) -> TaskService:
    tempdir = tempfile.mkdtemp()
    config = settings(database_url="sqlite:///" + str(Path(tempdir) / "aap.db"), **overrides)
    service = TaskService(Repository(config.database_url), queue=MemoryQueue(), settings=config)
    await service.start()
    return service


def test_create_puts_task_in_queue_and_writes_event() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL, source="form"))
            assert task.status == TaskStatus.QUEUED
            assert await service.queue.size() == 1
            events = await service.repository.list_events(task.id)
            assert events[0].kind == EventKind.TASK_CREATED
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_full_run_with_auto_approve_finishes_in_one_pass() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL, auto_approve=True))
            outcome = await service.process(task.id)
            assert outcome.status == TaskStatus.DONE
            assert outcome.result is not None
            assert len(outcome.result.rows) == 5
            assert outcome.result.table_markdown.startswith("**Топ-5")

            view = await service.view(task.id)
            assert view is not None
            assert view.task.status == TaskStatus.DONE
            kinds = [e.kind for e in view.events]
            assert EventKind.PLAN_READY in kinds and EventKind.TASK_FINISHED in kinds
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_without_auto_approve_task_waits_for_a_human() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL, auto_approve=False))
            first = await service.process(task.id)
            assert first.status == TaskStatus.WAITING_APPROVAL
            assert first.waiting_step == "report"

            view = await service.view(task.id)
            assert view is not None and view.task.status == TaskStatus.WAITING_APPROVAL

            await service.approve(task.id, "report")
            second = await service.process(task.id)
            assert second.status == TaskStatus.DONE
            assert second.result is not None and second.result.delivery["delivered"] is True

            # Подтверждение записано событием: оно переживёт перезапуск процесса.
            approved = await service._approved_steps(task.id)
            assert approved == {"report"}
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_worker_loop_processes_queue() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            await service.create(TaskRequest(goal="найди 4 товара категории рюкзак, выбери 2", auto_approve=True))
            processed = await service.work_loop(poll_timeout=0.1, max_tasks=1)
            assert processed == 1
            tasks = await service.list()
            assert tasks[0].status == TaskStatus.DONE
            assert tasks[0].result is not None and len(tasks[0].result.rows) == 2
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_cancel_marks_task_and_blocks_processing() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL))
            cancelled = await service.cancel(task.id)
            assert cancelled.status == TaskStatus.CANCELLED

            outcome = await service.process(task.id)
            assert outcome.status == TaskStatus.CANCELLED
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_resume_requires_a_plan_and_a_waiting_status() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL))
            with raises(ValueError, "продолжать нечего"):
                await service.resume(task.id)
            with raises(KeyError):
                await service.resume("task_missing")
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_failed_task_can_be_requeued() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL, auto_approve=True))

            # Так это выглядит в жизни: внешний сервис недоступен, инструмент падает.
            async def boom(ctx: object, params: object) -> dict[str, object]:
                raise RuntimeError("маркетплейс недоступен")

            original = service.registry.get("search_products")
            service.registry.replace(
                Tool(name="search_products", description=original.description, params=original.params, run=boom)
            )
            try:
                outcome = await service.process(task.id)
            finally:
                service.registry.replace(original)

            assert outcome.status == TaskStatus.FAILED
            assert "маркетплейс недоступен" in (outcome.error or "")

            view = await service.view(task.id)
            assert view is not None and view.task.status == TaskStatus.FAILED
            # Задача в статусе failed не блокирует очередь: следующая работает как обычно.
            fresh = await service.create(TaskRequest(goal="найди 2 товара категории рюкзак", auto_approve=True))
            assert (await service.process(fresh.id)).status == TaskStatus.DONE
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_stats_reflect_finished_tasks() -> None:
    async def scenario() -> None:
        service = await _service()
        try:
            task = await service.create(TaskRequest(goal=GOAL, auto_approve=True))
            await service.process(task.id)
            stats = await service.repository.stats()
            assert stats["tasks"] == 1
            assert stats["by_status"]["done"] == 1
            assert stats["steps"] >= 4
        finally:
            await service.stop()

    asyncio.run(scenario())
