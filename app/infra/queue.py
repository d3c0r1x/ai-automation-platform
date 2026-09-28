"""Очередь задач: Redis в проде, очередь в процессе — локально и в тестах.

Redis здесь не «для галочки»: он разводит приём задач и их выполнение по разным
процессам. API отвечает `202 Accepted` за миллисекунды, а долгий агент работает
в воркере, который можно перезапускать и масштабировать отдельно от API.

Очередь хранит **только идентификатор задачи** — всё состояние в базе. Поэтому
потеря элемента очереди не означает потерю задачи: её можно поставить заново
(`POST /api/tasks/{id}/retry`), и выполнение продолжится с последнего шага.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol


class Queue(Protocol):
    async def push(self, task_id: str) -> None: ...
    async def pop(self, timeout: float = 1.0) -> str | None: ...
    async def size(self) -> int: ...
    async def close(self) -> None: ...
    @property
    def backend(self) -> str: ...


class MemoryQueue:
    """Очередь в процессе: для демо, тестов и одиночного запуска."""

    def __init__(self) -> None:
        self._items: asyncio.Queue[str] = asyncio.Queue()

    @property
    def backend(self) -> str:
        return "memory"

    async def push(self, task_id: str) -> None:
        await self._items.put(task_id)

    async def pop(self, timeout: float = 1.0) -> str | None:
        try:
            return await asyncio.wait_for(self._items.get(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return None

    async def size(self) -> int:
        return self._items.qsize()

    async def close(self) -> None:
        return None


class RedisQueue:
    """FIFO на списке Redis: задачи кладём справа, забираем слева."""

    def __init__(self, url: str, name: str = "aap:tasks") -> None:
        self._url = url
        self._name = name
        self._client: Any = None

    @property
    def backend(self) -> str:
        return "redis"

    async def connect(self) -> None:
        import redis.asyncio as redis  # lazy: необязательная зависимость

        self._client = redis.from_url(self._url, decode_responses=True)
        await self._client.ping()

    async def push(self, task_id: str) -> None:
        await self._client.rpush(self._name, task_id)

    async def pop(self, timeout: float = 1.0) -> str | None:
        result = await self._client.blpop(self._name, timeout=max(1, int(timeout)))
        return result[1] if result else None

    async def size(self) -> int:
        return int(await self._client.llen(self._name))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def build_queue(settings: Any) -> Queue:
    if settings.queue == "redis":
        queue = RedisQueue(settings.redis_url, settings.queue_name)
        await queue.connect()
        return queue
    return MemoryQueue()
