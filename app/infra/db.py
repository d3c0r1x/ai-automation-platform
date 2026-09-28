"""Хранилище задач: SQLite локально, PostgreSQL в проде.

Один SQL и два драйвера. Разница между ними ровно одна — плейсхолдеры
(`?` против `$1`), и она снимается в `PostgresDriver` одной функцией. Это
осознанный выбор против SQLAlchemy: платформе нужны четыре таблицы и
десяток запросов, а слой ORM здесь добавил бы больше кода, чем убрал.

Что важно по существу:

- **Задача восстанавливается после перезапуска.** В базе лежат план, статус,
  результаты шагов и события, поэтому упавший воркер продолжает с того же шага.
- **События — источник прогресса.** SSE-поток читает их из базы с номером
  `seq`, а не из памяти процесса: перезагрузка страницы не теряет историю.
- **Драйверы подключаются лениво** (`aiosqlite`, `asyncpg`), поэтому ядро и
  тесты работают без установленного Postgres.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from app.core.models import (
    EventKind,
    Plan,
    StepResult,
    Task,
    TaskEvent,
    TaskResult,
    TaskStatus,
    TaskView,
)

SCHEMA_SQLITE = (
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'api',
        status TEXT NOT NULL,
        notify TEXT,
        auto_approve INTEGER,
        plan TEXT,
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_steps (
        task_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        ok INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 1,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        output TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (task_id, step_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_events (
        task_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        payload TEXT,
        at TEXT NOT NULL,
        PRIMARY KEY (task_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at)",
)

SCHEMA_POSTGRES = (
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'api',
        status TEXT NOT NULL,
        notify TEXT,
        auto_approve BOOLEAN,
        plan TEXT,
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_steps (
        task_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        ok BOOLEAN NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 1,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        output TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (task_id, step_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_events (
        task_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        payload TEXT,
        at TEXT NOT NULL,
        PRIMARY KEY (task_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at)",
)


class Driver(Protocol):
    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None: ...
    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None: ...
    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


class SqliteDriver:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Any = None

    async def connect(self) -> None:
        import aiosqlite

        if self._path not in (":memory:", ""):
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        await self._conn.execute(sql, tuple(params))
        await self._conn.commit()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        cursor = await self._conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


class PostgresDriver:
    """asyncpg. Плейсхолдеры `?` из общего SQL переводятся в `$1…$n`."""

    PLACEHOLDER = re.compile(r"\?")

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    @classmethod
    def translate(cls, sql: str) -> str:
        counter = iter(range(1, 1000))
        return cls.PLACEHOLDER.sub(lambda _: f"${next(counter)}", sql)

    async def connect(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        await self._pool.execute(self.translate(sql), *tuple(params))

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(self.translate(sql), *tuple(params))
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(self.translate(sql), *tuple(params))
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _loads(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class Repository:
    def __init__(self, url: str) -> None:
        self.url = url
        self._driver: Driver | None = None
        self._is_postgres = url.startswith(("postgres://", "postgresql://"))

    @property
    def is_postgres(self) -> bool:
        return self._is_postgres

    async def connect(self) -> None:
        if self._is_postgres:
            self._driver = PostgresDriver(self.url)
        else:
            path = self.url.split("sqlite:///", 1)[-1] or "data/aap.db"
            self._driver = SqliteDriver(path)
        await self._driver.connect()
        for statement in SCHEMA_POSTGRES if self._is_postgres else SCHEMA_SQLITE:
            await self._driver.execute(statement)

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            raise RuntimeError("хранилище не подключено: вызовите connect()")
        return self._driver

    # ── Задачи ──────────────────────────────────────────────────────────────
    async def create_task(self, task: Task) -> Task:
        await self.driver.execute(
            "INSERT INTO tasks (id, goal, source, status, notify, auto_approve, plan, result, error,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.goal,
                task.source,
                task.status.value,
                task.notify,
                None if task.auto_approve is None else int(task.auto_approve),
                None,
                None,
                None,
                _iso(task.created_at),
                _iso(task.updated_at),
            ),
        )
        return task

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        plan: Plan | None = None,
        result: TaskResult | None = None,
        error: str | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        params: list[Any] = [_iso(datetime.now(timezone.utc))]
        if status is not None:
            fields.append("status = ?")
            params.append(status.value)
        if plan is not None:
            fields.append("plan = ?")
            params.append(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False))
        if result is not None:
            fields.append("result = ?")
            params.append(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        if error is not None:
            fields.append("error = ?")
            params.append(error)
        params.append(task_id)
        await self.driver.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params)

    def _row_to_task(self, row: dict[str, Any]) -> Task:
        plan_raw = _loads(row.get("plan"), None)
        result_raw = _loads(row.get("result"), None)
        return Task(
            id=row["id"],
            goal=row["goal"],
            source=row.get("source") or "api",
            status=TaskStatus(row["status"]),
            notify=row.get("notify"),
            auto_approve=None if row.get("auto_approve") is None else bool(row["auto_approve"]),
            plan=Plan.model_validate(plan_raw) if plan_raw else None,
            result=TaskResult.model_validate(result_raw) if result_raw else None,
            error=row.get("error"),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    async def get_task(self, task_id: str) -> Task | None:
        row = await self.driver.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._row_to_task(row) if row else None

    async def list_tasks(self, limit: int = 50, status: TaskStatus | None = None) -> list[Task]:
        if status is None:
            rows = await self.driver.fetchall(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (int(limit),)
            )
        else:
            rows = await self.driver.fetchall(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.value, int(limit)),
            )
        return [self._row_to_task(r) for r in rows]

    # ── Шаги ────────────────────────────────────────────────────────────────
    async def save_step(self, task_id: str, step: StepResult) -> None:
        await self.driver.execute(
            "DELETE FROM task_steps WHERE task_id = ? AND step_id = ?", (task_id, step.step_id)
        )
        await self.driver.execute(
            "INSERT INTO task_steps (task_id, step_id, tool, ok, attempts, duration_ms, output, error,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                step.step_id,
                step.tool,
                int(step.ok),
                step.attempts,
                step.duration_ms,
                json.dumps(step.output, ensure_ascii=False),
                step.error,
                _iso(datetime.now(timezone.utc)),
            ),
        )

    async def list_steps(self, task_id: str) -> list[StepResult]:
        rows = await self.driver.fetchall(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY created_at", (task_id,)
        )
        return [
            StepResult(
                step_id=r["step_id"],
                tool=r["tool"],
                ok=bool(r["ok"]),
                attempts=int(r["attempts"]),
                duration_ms=int(r["duration_ms"]),
                output=_loads(r.get("output"), {}),
                error=r.get("error"),
            )
            for r in rows
        ]

    # ── События ─────────────────────────────────────────────────────────────
    async def add_event(self, event: TaskEvent) -> TaskEvent:
        row = await self.driver.fetchone(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM task_events WHERE task_id = ?",
            (event.task_id,),
        )
        seq = int((row or {}).get("next") or 1)
        await self.driver.execute(
            "INSERT INTO task_events (task_id, seq, kind, message, payload, at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.task_id,
                seq,
                event.kind.value,
                event.message,
                json.dumps(event.payload, ensure_ascii=False),
                _iso(event.at),
            ),
        )
        return event.model_copy(update={"seq": seq})

    async def list_events(self, task_id: str, after_seq: int = 0, limit: int = 500) -> list[TaskEvent]:
        rows = await self.driver.fetchall(
            "SELECT * FROM task_events WHERE task_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (task_id, int(after_seq), int(limit)),
        )
        return [
            TaskEvent(
                task_id=r["task_id"],
                kind=EventKind(r["kind"]),
                message=r.get("message") or "",
                payload=_loads(r.get("payload"), {}),
                at=_dt(r["at"]),
                seq=int(r["seq"]),
            )
            for r in rows
        ]

    async def task_view(self, task_id: str) -> TaskView | None:
        task = await self.get_task(task_id)
        if task is None:
            return None
        return TaskView(
            task=task,
            steps=await self.list_steps(task_id),
            events=await self.list_events(task_id),
        )

    # ── Сводка ──────────────────────────────────────────────────────────────
    async def stats(self) -> dict[str, Any]:
        rows = await self.driver.fetchall("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        by_status = {r["status"]: int(r["n"]) for r in rows}
        events = await self.driver.fetchone("SELECT COUNT(*) AS n FROM task_events")
        steps = await self.driver.fetchone("SELECT COUNT(*) AS n FROM task_steps")
        return {
            "tasks": sum(by_status.values()),
            "by_status": by_status,
            "steps": int((steps or {}).get("n") or 0),
            "events": int((events or {}).get("n") or 0),
            "backend": "postgresql" if self._is_postgres else "sqlite",
        }
