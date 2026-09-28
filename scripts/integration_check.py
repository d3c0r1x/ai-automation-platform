"""Проверка полного контура: PostgreSQL + Redis + воркер.

    AAP_DATABASE_URL=postgresql://aap:aap@localhost:5432/aap \
    AAP_QUEUE=redis AAP_REDIS_URL=redis://localhost:6379/0 \
    python scripts/integration_check.py

Зачем отдельно от `pytest`: тесты ядра сознательно не требуют ни Postgres, ни
Redis (иначе их нельзя запустить «просто так»). Но перед выкаткой важно знать,
что настоящая связка работает: задача кладётся в Redis, воркер её берёт,
результат оказывается в Postgres.

Скрипт ничего не выдумывает: если базы или Redis нет — он падает с понятной
ошибкой, а не «проходит» на SQLite.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import load_settings  # noqa: E402
from app.core.models import TaskRequest, TaskStatus  # noqa: E402
from app.services.tasks import build_service  # noqa: E402

GOAL = "Найди 8 товаров категории наушники до 4000 ₽, выбери 3 лучших и составь таблицу"


async def main() -> int:
    settings = load_settings()
    print(f"хранилище: {settings.database_url}")
    print(f"очередь:   {settings.queue} ({settings.redis_url})")

    if not settings.is_postgres or settings.queue != "redis":
        print("НУЖНЫ PostgreSQL и Redis: запустите docker compose up -d postgres redis")
        return 2

    service = await build_service(settings, with_queue=True)
    try:
        task = await service.create(TaskRequest(goal=GOAL, auto_approve=True))
        queued = await service.queue.size()  # type: ignore[union-attr]
        print(f"задача {task.id} в очереди, элементов: {queued}")

        processed = await service.work_loop(poll_timeout=1.0, max_tasks=1)
        view = await service.view(task.id)
        assert processed == 1 and view is not None

        print(f"статус: {view.task.status.value}")
        print(f"шагов записано: {len(view.steps)}, событий: {len(view.events)}")
        assert view.task.status == TaskStatus.DONE
        assert view.task.result is not None and len(view.task.result.rows) == 3

        stats = await service.repository.stats()
        print(f"статистика базы: {stats}")
        assert stats["backend"] == "postgresql"

        print("ИНТЕГРАЦИЯ OK: Redis + PostgreSQL + воркер работают вместе")
        return 0
    finally:
        await service.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
