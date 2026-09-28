"""HTTP-слой: REST + SSE-поток прогресса.

API не выполняет задачи. Он принимает их, ставит в очередь и отдаёт состояние.
Долгий агент живёт в воркере, поэтому API можно перезапускать под нагрузкой, а
прогресс читается из базы — перезагрузка страницы не теряет историю.

Прогресс отдаётся **опросом базы** (`/events/stream`), а не событиями в памяти
процесса. Причина прагматичная: воркер и API — разные процессы (и в проде даже
разные контейнеры), поэтому шина в памяти между ними не работает, а Redis-pub/sub
ради прогресс-бара — лишняя инфраструктура. Шаг опроса — `AAP_SSE_INTERVAL`.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.models import TERMINAL_STATUSES, TaskRequest, TaskStatus
from app.services.tasks import build_service

SSE_INTERVAL = float(os.getenv("AAP_SSE_INTERVAL", "0.5"))


class ApproveRequest(BaseModel):
    step_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    service = await build_service(with_queue=True)
    app.state.service = service
    try:
        yield
    finally:
        await service.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Automation Platform",
        version="1.0.0",
        description="Приём задач → планирование → выполнение инструментами → результат.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def service(request: Request) -> Any:
        return request.app.state.service

    # ── Служебные ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["service"])
    async def health(request: Request) -> dict[str, Any]:
        svc = service(request)
        return {
            "status": "ok",
            "queue": svc.queue.backend if svc.queue else "none",
            "storage": "postgresql" if svc.repository.is_postgres else "sqlite",
            "llm": "configured" if not svc.settings.demo_mode else "demo (детерминированный планировщик)",
        }

    @app.get("/api/stats", tags=["service"])
    async def stats(request: Request) -> dict[str, Any]:
        return await service(request).repository.stats()

    @app.get("/api/tools", tags=["service"])
    async def tools(request: Request) -> dict[str, Any]:
        return {"tools": service(request).registry.describe()}

    # ── Задачи ──────────────────────────────────────────────────────────────
    @app.post("/api/tasks", status_code=202, tags=["tasks"])
    async def create_task(request: Request, payload: TaskRequest) -> dict[str, Any]:
        task = await service(request).create(payload)
        return {"task_id": task.id, "status": task.status.value, "links": {"self": f"/api/tasks/{task.id}"}}

    @app.get("/api/tasks", tags=["tasks"])
    async def list_tasks(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        status: TaskStatus | None = None,
    ) -> dict[str, Any]:
        tasks = await service(request).list(limit=limit, status=status)
        return {"count": len(tasks), "tasks": [t.model_dump(mode="json") for t in tasks]}

    @app.get("/api/tasks/{task_id}", tags=["tasks"])
    async def get_task(request: Request, task_id: str) -> dict[str, Any]:
        view = await service(request).view(task_id)
        if view is None:
            raise HTTPException(status_code=404, detail="задача не найдена")
        return view.model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/approve", tags=["tasks"])
    async def approve(request: Request, task_id: str, payload: ApproveRequest = Body(default=ApproveRequest())) -> dict[str, Any]:
        svc = service(request)
        view = await svc.view(task_id)
        if view is None:
            raise HTTPException(status_code=404, detail="задача не найдена")
        step_id = payload.step_id
        if not step_id:
            waiting = [e.payload.get("step_id") for e in view.events if e.kind.value == "approval_required"]
            if not waiting:
                raise HTTPException(status_code=409, detail="задача не ждёт подтверждения")
            step_id = str(waiting[-1])
        await svc.approve(task_id, step_id)
        if svc.queue:
            await svc.queue.push(task_id)  # воркер поднимет задачу и продолжит с этого шага
        return {"task_id": task_id, "approved_step": step_id, "status": "queued"}

    @app.post("/api/tasks/{task_id}/cancel", tags=["tasks"])
    async def cancel(request: Request, task_id: str) -> dict[str, Any]:
        try:
            task = await service(request).cancel(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="задача не найдена") from None
        except Exception as exc:  # noqa: BLE001 — InvalidTransition и прочее
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task.id, "status": task.status.value}

    @app.post("/api/tasks/{task_id}/retry", status_code=202, tags=["tasks"])
    async def retry(request: Request, task_id: str) -> dict[str, Any]:
        svc = service(request)
        view = await svc.view(task_id)
        if view is None:
            raise HTTPException(status_code=404, detail="задача не найдена")
        if view.task.status in TERMINAL_STATUSES and view.task.status != TaskStatus.FAILED:
            raise HTTPException(status_code=409, detail=f"задача уже в статусе {view.task.status.value}")
        if svc.queue:
            await svc.queue.push(task_id)
        return {"task_id": task_id, "status": "queued"}

    # ── События прогресса ───────────────────────────────────────────────────
    @app.get("/api/tasks/{task_id}/events", tags=["events"])
    async def events(request: Request, task_id: str, after: int = Query(default=0, ge=0)) -> dict[str, Any]:
        items = await service(request).repository.list_events(task_id, after_seq=after)
        return {"count": len(items), "events": [e.model_dump(mode="json") for e in items]}

    @app.get("/api/tasks/{task_id}/events/stream", tags=["events"])
    async def stream(request: Request, task_id: str, after: int = Query(default=0, ge=0)) -> StreamingResponse:
        svc = service(request)

        async def publisher() -> AsyncIterator[str]:
            cursor = after
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    break
                batch = await svc.repository.list_events(task_id, after_seq=cursor)
                for event in batch:
                    cursor = event.seq
                    yield f"event: {event.kind.value}\ndata: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                task = await svc.repository.get_task(task_id)
                if task is None:
                    break
                if not batch:
                    idle_ticks += 1
                if task.status in TERMINAL_STATUSES and not batch:
                    yield f"event: stream_end\ndata: {json.dumps({'status': task.status.value})}\n\n"
                    break
                if idle_ticks > 600:  # ~5 минут без событий — закрываем поток
                    yield "event: stream_end\ndata: {\"status\": \"idle\"}\n\n"
                    break
                await asyncio.sleep(SSE_INTERVAL)

        return StreamingResponse(
            publisher(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Внешние входы ───────────────────────────────────────────────────────
    @app.post("/api/webhooks/{source}", status_code=202, tags=["ingest"])
    async def webhook(request: Request, source: str) -> dict[str, Any]:
        """Принять задачу из внешнего контура: форма, бот, CRM.

        Понимает JSON `{"goal": "..."}` и простой текст в теле запроса —
        так форму можно отправить обычным `fetch` без обёрток.
        """
        content_type = request.headers.get("content-type", "")
        goal = ""
        notify = None
        if "application/json" in content_type:
            payload = await request.json()
            goal = str(payload.get("goal") or payload.get("text") or "")
            notify = payload.get("notify")
        else:
            goal = (await request.body()).decode("utf-8", "replace").strip()
        if len(goal.strip()) < 3:
            raise HTTPException(status_code=422, detail="нужен текст задачи (не короче 3 символов)")
        notes = {"telegram": "telegram", "form": "form", "webhook": "webhook"}
        task = await service(request).create(
            TaskRequest(goal=goal, source=notes.get(source, "webhook"), notify=notify)
        )
        return {"task_id": task.id, "status": task.status.value}

    # ── Дашборд ─────────────────────────────────────────────────────────────
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():  # pragma: no cover — проверяется в Docker-сборке
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")

    return app


app = create_app()
