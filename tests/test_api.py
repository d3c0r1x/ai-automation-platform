"""HTTP-слой: приём задач, статусы, подтверждение, вебхук, служебные эндпоинты.

Тесты идут через настоящий ASGI-стек (httpx + ASGITransport), а не через вызовы
функций-обработчиков: так проверяются и валидация pydantic, и коды ответов, и
форма JSON, которые видит дашборд.

Если FastAPI в окружении нет (локальный запуск «только рантайм»), тесты
честно помечаются пропущенными, а не падают.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import httpx

from app.core.models import TaskStatus
from tests.expect import raises
from tests.helpers import require

GOAL = "Найди 20 товаров категории наушники до 5000 ₽, выбери 5 лучших, составь таблицу и отправь мне отчёт"


def _prepare_app() -> object:
    require("fastapi")
    tempdir = tempfile.mkdtemp()
    os.environ["AAP_DATABASE_URL"] = "sqlite:///" + str(Path(tempdir) / "api.db")
    os.environ["AAP_QUEUE"] = "memory"

    from app.api.main import create_app

    return create_app()


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]


def test_health_and_tools() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                health = await client.get("/health")
                assert health.status_code == 200
                assert health.json()["queue"] == "memory"
                assert "demo" in health.json()["llm"]

                tools = await client.get("/api/tools")
                names = {t["name"] for t in tools.json()["tools"]}
                assert {"search_products", "send_report", "build_table"} <= names
                gated = {t["name"] for t in tools.json()["tools"] if t["requires_approval"]}
                assert gated == {"send_report"}

    asyncio.run(scenario())


def test_task_lifecycle_through_http() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                created = await client.post("/api/tasks", json={"goal": GOAL, "auto_approve": True})
                assert created.status_code == 202
                task_id = created.json()["task_id"]

                # Задачу выполняет воркер: в тесте его роль играет один проход цикла.
                assert await app.state.service.work_loop(poll_timeout=0.1, max_tasks=1) == 1

                view = await client.get(f"/api/tasks/{task_id}")
                assert view.status_code == 200
                body = view.json()
                assert body["task"]["status"] == TaskStatus.DONE.value
                assert body["task"]["result"]["rows"]
                assert body["task"]["result"]["data"]["search"]["source"] == "demo"
                assert any(e["kind"] == "task_finished" for e in body["events"])

                listing = await client.get("/api/tasks", params={"limit": 5})
                assert listing.json()["count"] == 1

                stats = await client.get("/api/stats")
                assert stats.json()["by_status"]["done"] == 1

    asyncio.run(scenario())


def test_approval_flow_through_http() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                task_id = (
                    await client.post("/api/tasks", json={"goal": GOAL, "auto_approve": False})
                ).json()["task_id"]
                await app.state.service.work_loop(poll_timeout=0.1, max_tasks=1)

                waiting = await client.get(f"/api/tasks/{task_id}")
                assert waiting.json()["task"]["status"] == TaskStatus.WAITING_APPROVAL.value

                approved = await client.post(f"/api/tasks/{task_id}/approve", json={})
                assert approved.status_code == 200
                assert approved.json()["approved_step"] == "report"

                await app.state.service.work_loop(poll_timeout=0.1, max_tasks=1)
                finished = await client.get(f"/api/tasks/{task_id}")
                assert finished.json()["task"]["status"] == TaskStatus.DONE.value

    asyncio.run(scenario())


def test_cancel_and_404() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                task_id = (await client.post("/api/tasks", json={"goal": GOAL})).json()["task_id"]
                cancelled = await client.post(f"/api/tasks/{task_id}/cancel")
                assert cancelled.status_code == 200
                assert cancelled.json()["status"] == TaskStatus.CANCELLED.value

                missing = await client.get("/api/tasks/task_missing")
                assert missing.status_code == 404
                missing_cancel = await client.post("/api/tasks/task_missing/cancel")
                assert missing_cancel.status_code == 404
                assert "не найдена" in missing_cancel.json()["detail"]

    asyncio.run(scenario())


def test_validation_rejects_short_goal() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                bad = await client.post("/api/tasks", json={"goal": "ok"})
                assert bad.status_code == 422

    asyncio.run(scenario())


def test_webhook_accepts_plain_text_and_json() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                json_form = await client.post("/api/webhooks/form", json={"goal": GOAL})
                assert json_form.status_code == 202

                text_form = await client.post(
                    "/api/webhooks/telegram",
                    content="найди 5 товаров категории рюкзак".encode("utf-8"),
                    headers={"content-type": "text/plain"},
                )
                assert text_form.status_code == 202

                empty = await client.post("/api/webhooks/form", content=b"")
                assert empty.status_code == 422

                tasks = await client.get("/api/tasks")
                sources = {t["source"] for t in tasks.json()["tasks"]}
                assert sources == {"form", "telegram"}

    asyncio.run(scenario())


def test_events_endpoint_and_stream_headers() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                task_id = (
                    await client.post("/api/tasks", json={"goal": GOAL, "auto_approve": True})
                ).json()["task_id"]
                await app.state.service.work_loop(poll_timeout=0.1, max_tasks=1)

                events = await client.get(f"/api/tasks/{task_id}/events")
                assert events.json()["count"] >= 4
                after = await client.get(f"/api/tasks/{task_id}/events", params={"after": 2})
                assert after.json()["count"] < events.json()["count"]

                async with client.stream("GET", f"/api/tasks/{task_id}/events/stream") as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    chunk = ""
                    async for line in response.aiter_lines():
                        chunk += line
                        if "stream_end" in chunk:
                            break
                    assert "task_created" in chunk

    asyncio.run(scenario())


def test_invalid_transition_is_reported_as_conflict() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                task_id = (
                    await client.post("/api/tasks", json={"goal": GOAL, "auto_approve": True})
                ).json()["task_id"]
                await app.state.service.work_loop(poll_timeout=0.1, max_tasks=1)

                # Завершённую задачу отменить нельзя — это 409, а не тихая ошибка.
                conflict = await client.post(f"/api/tasks/{task_id}/cancel")
                assert conflict.status_code == 409
                assert "done" in conflict.json()["detail"]

                unsupported = await client.post("/api/tasks/task_x/approve", json={})
                assert unsupported.status_code == 404

    asyncio.run(scenario())


def test_stream_endpoint_unknown_task_ends_stream() -> None:
    app = _prepare_app()

    async def scenario() -> None:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            async with _client(app) as client:
                async with client.stream("GET", "/api/tasks/task_missing/events/stream") as response:
                    assert response.status_code == 200
                    body = "".join([line async for line in response.aiter_lines()])
                    assert body == ""

    asyncio.run(scenario())


def test_require_helper_raises_skip_for_unknown_module() -> None:
    with raises(Exception):
        require("module_that_definitely_does_not_exist_1234")
