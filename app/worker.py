"""Воркер: отдельный процесс, который выполняет задачи из очереди.

Запуск:

    python -m app.worker               # бесконечно
    python -m app.worker --once        # обработать то, что есть, и выйти (CI, тесты)

Именно он исполняет план, а API только принимает задачи — поэтому очередь
нужна по-настоящему: воркеров может быть несколько, и каждый берёт свою задачу.
Задача, поставленная дважды, безопасна: выполненные шаги лежат в базе и
повторно не исполняются (см. `Executor.run`).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.core.config import load_settings
from app.services.tasks import build_service


async def main() -> int:
    parser = argparse.ArgumentParser(description="Воркер платформы автоматизации")
    parser.add_argument("--once", action="store_true", help="обработать доступные задачи и выйти")
    parser.add_argument("--poll", type=float, default=1.0, help="таймаут ожидания задачи, секунды")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    service = await build_service(settings, with_queue=True)
    try:
        processed = await service.work_loop(
            poll_timeout=args.poll, max_tasks=1 if args.once else None
        )
        logging.info("обработано задач: %s", processed)
        return 0
    finally:
        await service.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
