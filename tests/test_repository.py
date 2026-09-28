"""Хранилище: задачи, шаги, события и перевод плейсхолдеров для PostgreSQL."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.core.models import EventKind, Plan, PlanStep, StepResult, Task, TaskEvent, TaskResult, TaskStatus
from app.infra.db import PostgresDriver, Repository, new_task_id


def _repository() -> Repository:
    with tempfile.TemporaryDirectory() as raw:
        url = "sqlite:///" + str(Path(raw) / "test.db")
        return Repository(url)


async def _fresh() -> Repository:
    """Файл базы в отдельном каталоге на каждый тест: без общего состояния."""
    tempdir = tempfile.mkdtemp()
    repo = Repository("sqlite:///" + str(Path(tempdir) / "aap.db"))
    await repo.connect()
    return repo


def test_sqlite_url_is_parsed_as_sqlite() -> None:
    assert _repository().is_postgres is False
    assert Repository("postgresql://user:pass@localhost/aap").is_postgres is True


def test_postgres_placeholders_are_translated() -> None:
    sql = "INSERT INTO tasks (id, goal) VALUES (?, ?) AND status = ?"
    assert PostgresDriver.translate(sql) == "INSERT INTO tasks (id, goal) VALUES ($1, $2) AND status = $3"


def test_task_ids_are_unique() -> None:
    ids = {new_task_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("task_") for i in ids)


async def _create(repo: Repository, goal: str = "найди 3 товара") -> Task:
    task = Task(id=new_task_id(), goal=goal, source="form")
    return await repo.create_task(task)


def test_create_and_get_task() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            task = await _create(repo)
            loaded = await repo.get_task(task.id)
            assert loaded is not None
            assert loaded.goal == "найди 3 товара"
            assert loaded.status == TaskStatus.QUEUED
            assert await repo.get_task("task_missing") is None
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_plan_and_result_are_persisted_as_json() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            task = await _create(repo)
            plan = Plan(goal=task.goal, steps=[PlanStep(id="s1", tool="build_table", args={"rows": []})])
            result = TaskResult(summary="готово", table_markdown="| a |", rows=[{"a": 1}])
            await repo.update_task(task.id, status=TaskStatus.DONE, plan=plan, result=result)

            loaded = await repo.get_task(task.id)
            assert loaded is not None and loaded.plan is not None and loaded.result is not None
            assert loaded.plan.steps[0].tool == "build_table"
            assert loaded.result.table_markdown == "| a |"
            assert loaded.result.rows == [{"a": 1}]
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_list_tasks_filters_by_status_and_orders_by_recency() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            first = await _create(repo, "первая")
            second = await _create(repo, "вторая")
            await repo.update_task(first.id, status=TaskStatus.DONE)

            done = await repo.list_tasks(status=TaskStatus.DONE)
            assert [t.id for t in done] == [first.id]
            assert len(await repo.list_tasks()) == 2
            assert {t.id for t in await repo.list_tasks()} == {first.id, second.id}
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_steps_are_upserted() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            task = await _create(repo)
            await repo.save_step(task.id, StepResult(step_id="s1", tool="search_products", ok=True, output={"count": 5}))
            await repo.save_step(task.id, StepResult(step_id="s1", tool="search_products", ok=True, attempts=2, output={"count": 7}))

            steps = await repo.list_steps(task.id)
            assert len(steps) == 1
            assert steps[0].output["count"] == 7 and steps[0].attempts == 2
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_events_get_monotonic_sequence_and_after_seq() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            task = await _create(repo)
            saved = [await repo.add_event(TaskEvent(task_id=task.id, kind=EventKind.STEP_STARTED, message=f"шаг {i}")) for i in range(3)]
            assert [e.seq for e in saved] == [1, 2, 3]

            tail = await repo.list_events(task.id, after_seq=1)
            assert [e.seq for e in tail] == [2, 3]
            assert await repo.list_events("task_other") == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_task_view_contains_task_steps_and_events() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            task = await _create(repo)
            await repo.save_step(task.id, StepResult(step_id="s1", tool="rank_products", ok=True))
            await repo.add_event(TaskEvent(task_id=task.id, kind=EventKind.PLAN_READY, message="план готов"))

            view = await repo.task_view(task.id)
            assert view is not None
            assert view.task.id == task.id
            assert len(view.steps) == 1 and len(view.events) == 1
            assert await repo.task_view("task_missing") is None
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_stats_counts_tasks_steps_and_events() -> None:
    async def scenario() -> None:
        repo = await _fresh()
        try:
            task = await _create(repo)
            await repo.update_task(task.id, status=TaskStatus.FAILED, error="сломалось")
            await repo.save_step(task.id, StepResult(step_id="s1", tool="search_products", ok=False, error="нет сети"))
            await repo.add_event(TaskEvent(task_id=task.id, kind=EventKind.STEP_FAILED, message="упал"))

            stats = await repo.stats()
            assert stats["tasks"] == 1
            assert stats["by_status"] == {"failed": 1}
            assert stats["steps"] == 1 and stats["events"] == 1
            assert stats["backend"] == "sqlite"
        finally:
            await repo.close()

    asyncio.run(scenario())


class _RecordingDriver:
    """Драйвер-шпион: запоминает SQL и параметры, ничего никуда не пишет."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: object = ()) -> None:
        self.calls.append((sql, tuple(params)))  # type: ignore[arg-type]

    async def fetchone(self, sql: str, params: object = ()) -> None:
        return None

    async def fetchall(self, sql: str, params: object = ()) -> list:
        return []

    async def close(self) -> None:
        return None


def test_boolean_columns_are_bound_as_bool_not_int() -> None:
    """PostgreSQL не принимает 0/1 в BOOLEAN-колонку (нашла интеграционная проверка).

    SQLite такую ошибку прощает, поэтому проверяем тип самих значений, а не
    результат записи.
    """

    async def scenario() -> None:
        driver = _RecordingDriver()
        repo = Repository("postgresql://user:pass@localhost/aap")
        repo._driver = driver  # type: ignore[assignment]

        task = Task(id="task_types", goal="проверка типов", source="api", auto_approve=True)
        await repo.create_task(task)
        create_params = driver.calls[0][1]
        assert create_params[5] is True, f"auto_approve должен быть bool, а не {type(create_params[5])}"

        plain = Task(id="task_types_none", goal="без флага", source="api", auto_approve=None)
        await repo.create_task(plain)
        assert driver.calls[1][1][5] is None

        await repo.save_step("task_types", StepResult(step_id="s1", tool="build_table", ok=False))
        insert_sql, step_params = driver.calls[-1]  # DELETE + INSERT: интересует последний
        assert "INSERT INTO task_steps" in insert_sql
        assert step_params[3] is False, f"ok должен быть bool, а не {type(step_params[3])}"

    asyncio.run(scenario())


def test_postgres_sql_uses_numbered_placeholders_consistently() -> None:
    for statement in ("SELECT * FROM tasks WHERE id = ? AND status = ?", "INSERT INTO a (x, y) VALUES (?, ?)"):
        translated = PostgresDriver.translate(statement)
        assert "?" not in translated
        assert translated.count("$") == statement.count("?")


def test_repository_requires_connection() -> None:
    repo = Repository("sqlite:///:memory:")
    from tests.expect import raises

    with raises(RuntimeError, "не подключено"):
        asyncio.run(repo.get_task("task_x"))
