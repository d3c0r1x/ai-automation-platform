"""Демо-прогон платформы без ключей, базы данных и сети.

    python -m app.demo "Найди 20 товаров категории наушники до 5000 ₽, выбери 5 лучших, составь таблицу"

Что происходит: задача ставится в очередь → детерминированный планировщик
строит план → инструменты выполняются на демо-каталоге → таблица и отчёт
собираются в SQLite → печатается таймлайн событий.

Зачем это нужно:

- показать платформу целиком там, где нет ни ключей, ни Postgres, ни Redis;
- дать CI проверку, что сквозной путь не сломан (шаг `demo` в workflow);
- служить воспроизводимым примером: один и тот же запрос даёт один и тот же
  результат, поэтому на него можно ссылаться в отчётах и тестах.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
from typing import Any

from app.core.config import Settings, load_settings
from app.core.models import TaskRequest, TaskStatus
from app.infra.db import Repository
from app.infra.queue import MemoryQueue
from app.services.tasks import TaskService

DEFAULT_GOAL = (
    "Найди 20 товаров категории наушники до 5000 ₽, сравни их, "
    "выбери 5 лучших по цене и отзывам, составь таблицу и отправь мне отчёт"
)


def _fix_console() -> None:
    """Русский текст и символ ₽ в консоли Windows по умолчанию не печатаются."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _settings(database: str) -> Settings:
    base = load_settings()
    return Settings(
        database_url=database,
        queue="memory",
        llm_base_url="",  # демо всегда идёт детерминированным планировщиком
        llm_api_key="",
        require_approval=base.require_approval,
    )


async def run(
    goal: str,
    database: str = "sqlite:///data/aap.db",
    auto_approve: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    service = TaskService(
        Repository(database),
        queue=MemoryQueue(),
        settings=_settings(database),
    )
    await service.start()
    # Инструменты печатают сами (например, доставка в консоль). Собираем этот
    # вывод в буфер: в режиме `--json` на stdout обязан быть только JSON, а
    # потерять «человеческий» вывод тоже нельзя — он едет в отчёт.
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            report = await _run_pipeline(service, goal, auto_approve)
        report["console_output"] = captured.getvalue()
        if verbose:
            print(report["console_output"], end="")
            _print_report(report)
        return report
    finally:
        await service.stop()


async def _run_pipeline(service: TaskService, goal: str, auto_approve: bool) -> dict[str, Any]:
    """Поставить задачу, выполнить её и собрать отчёт в словарь.

    Задача всегда создаётся с `auto_approve=False`: так демо честно проходит
    через подтверждение отправки — это часть продукта, и её видно. По умолчанию
    демо подтверждает шаг за человека (иначе ждать было бы некому), а с
    `--no-auto-approve` останавливается и показывает статус `waiting_approval`.
    """
    task = await service.create(TaskRequest(goal=goal, source="form", auto_approve=False))
    outcome = await service.process(task.id)

    approved_rounds = 0
    while outcome.status == TaskStatus.WAITING_APPROVAL and auto_approve:
        approved_rounds += 1
        await service.approve(task.id, outcome.waiting_step or "")
        outcome = await service.process(task.id)

    view = await service.view(task.id)
    assert view is not None, "задача должна существовать: её только что создали"
    result = outcome.result
    return {
        "task_id": task.id,
        "status": outcome.status.value,
        "plan_source": view.task.plan.source.value if view.task.plan else None,
        "plan": [step.model_dump() for step in (view.task.plan.steps if view.task.plan else [])],
        "steps": [step.model_dump() for step in view.steps],
        "summary": result.summary if result else "",
        "table_markdown": result.table_markdown if result else "",
        "rows": result.rows if result else [],
        "artifacts": result.artifacts if result else {},
        "skipped": [s.model_dump() for s in (result.skipped if result else [])],
        "delivery": result.delivery if result else {},
        "approvals": approved_rounds,
        "events": [{"seq": e.seq, "kind": e.kind.value, "message": e.message} for e in view.events],
    }


def _print_report(report: dict[str, Any]) -> None:
    line = "─" * 72
    print(line)
    print(f"Задача {report['task_id']} · статус: {report['status']}")
    print(f"Планировщик: {report['plan_source']}")
    print(line)
    print("ПЛАН")
    for index, step in enumerate(report["plan"], start=1):
        deps = f" (зависит от: {', '.join(step['depends_on'])})" if step["depends_on"] else ""
        gate = " [нужно подтверждение]" if step["requires_approval"] else ""
        print(f"  {index}. {step['tool']}{deps}{gate} — {step['reason']}")
    print(line)
    print("СОБЫТИЯ")
    for event in report["events"]:
        print(f"  {event['seq']:>2}. {event['kind']:<18} {event['message']}")
    print(line)
    print("РЕЗУЛЬТАТ")
    print(report["summary"])
    if report["table_markdown"]:
        print()
        print(report["table_markdown"])
    if report["artifacts"]:
        print()
        print("Артефакты: " + ", ".join(f"{name} → {path}" for name, path in report["artifacts"].items()))
    if report["delivery"]:
        print("Доставка: " + json.dumps(report["delivery"], ensure_ascii=False))
    print(line)
    total_ms = sum(step["duration_ms"] for step in report["steps"])
    print(f"Шагов выполнено: {len(report['steps'])} · суммарное время шагов: {total_ms} мс")


async def main() -> int:
    _fix_console()
    parser = argparse.ArgumentParser(description="Демо-прогон платформы без ключей и сети")
    parser.add_argument("goal", nargs="?", default=DEFAULT_GOAL, help="текст задачи")
    parser.add_argument("--db", default="sqlite:///data/aap.db", help="SQLite-файл для демо")
    parser.add_argument("--no-auto-approve", action="store_true", help="остановиться на подтверждении")
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    args = parser.parse_args()

    report = await run(
        args.goal,
        database=args.db,
        auto_approve=not args.no_auto_approve,
        verbose=not args.json,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
