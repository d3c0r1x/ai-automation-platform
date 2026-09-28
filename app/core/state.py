"""Машина состояний задачи.

Переходы описаны явно, а не «как получится»: одна и та же задача меняется из
API, воркера и планировщика, и без списка допустимых переходов легко получить
`done → running` после повторного запуска или отмену уже завершённой задачи.
"""

from __future__ import annotations

from app.core.models import TERMINAL_STATUSES, TaskStatus


class InvalidTransition(RuntimeError):
    pass


ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.PLANNING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.PLANNING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_APPROVAL: {TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: {TaskStatus.QUEUED},  # повторный запуск = новая попытка
    TaskStatus.CANCELLED: set(),
}


def can_transition(source: TaskStatus, target: TaskStatus) -> bool:
    if source == target:
        return True
    return target in ALLOWED.get(source, set())


def ensure_transition(source: TaskStatus, target: TaskStatus) -> None:
    if not can_transition(source, target):
        raise InvalidTransition(f"нельзя перейти {source.value} → {target.value}")


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATUSES
