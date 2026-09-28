"""Доменные модели платформы.

Здесь нет ни FastAPI, ни SQL, ни Redis: ядро (планировщик, исполнитель,
инструменты) зависит только от pydantic. Поэтому его можно тестировать и
запускать без базы и без сети — что и делает `python -m app.demo`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    """Жизненный цикл задачи. Переходы описаны в core/state.py."""

    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


class EventKind(str, Enum):
    TASK_CREATED = "task_created"
    PLAN_READY = "plan_ready"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    STEP_FAILED = "step_failed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    TASK_FINISHED = "task_finished"


class TaskEvent(BaseModel):
    """Событие прогресса: из него собирается SSE-поток и история задачи."""

    task_id: str
    kind: EventKind
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)
    seq: int = 0


class PlanStep(BaseModel):
    """Один шаг плана — вызов инструмента с аргументами.

    `reason` не для красоты: план должен объясняться человеку, поэтому модель
    обязана сформулировать, зачем нужен шаг. Если объяснение бессмысленное —
    это видно в дашборде и в логах.
    """

    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    depends_on: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    # Необязательный шаг (например, открыть страницу товара): если он не удался
    # после всех попыток, задача продолжается, а пропуск виден в результате.
    optional: bool = False


class PlanSource(str, Enum):
    LLM = "llm"
    DETERMINISTIC = "deterministic"


class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]
    expected_output: str = ""
    source: PlanSource = PlanSource.DETERMINISTIC
    notes: str = ""


class StepResult(BaseModel):
    step_id: str
    tool: str
    ok: bool
    attempts: int = 1
    duration_ms: int = 0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class TaskRequest(BaseModel):
    """Вход платформы: цель + откуда она пришла."""

    goal: str = Field(min_length=3, max_length=2000)
    source: Literal["api", "form", "telegram", "webhook"] = "api"
    notify: str | None = Field(default=None, description="webhook/telegram-канал для результата")
    auto_approve: bool | None = Field(
        default=None,
        description="None — по политике шага; False — останавливаться на подтверждении",
    )


class Task(BaseModel):
    id: str
    goal: str
    source: str = "api"
    status: TaskStatus = TaskStatus.QUEUED
    notify: str | None = None
    auto_approve: bool | None = None
    plan: Plan | None = None
    result: "TaskResult | None" = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SkippedStep(BaseModel):
    """Шаг, который не смог выполниться, но был необязательным."""

    step_id: str
    tool: str
    reason: str


class TaskResult(BaseModel):
    """Итог задачи — то, что видит человек: сводка, таблица, артефакты."""

    summary: str
    table_markdown: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    skipped: list[SkippedStep] = Field(default_factory=list)
    delivery: dict[str, Any] = Field(default_factory=dict)


Task.model_rebuild()


class TaskView(BaseModel):
    """Задача вместе с шагами и событиями — ответ GET /api/tasks/{id}."""

    task: Task
    steps: list[StepResult] = Field(default_factory=list)
    events: list[TaskEvent] = Field(default_factory=list)
