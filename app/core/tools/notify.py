"""Инструмент доставки результата.

Это **точка невозврата**: сообщение уходит наружу, и «отменить» его уже нельзя.
Поэтому у инструмента стоит `requires_approval=True`, а в демо-режиме отправка
в Telegram и вебхуки не выполняется вовсе — вместо неё возвращается
`simulated: true`. Ошибку «тихо ничего не отправили» пользователь бы не заметил,
поэтому запись о симуляции попадает и в отчёт, и в события задачи.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams

Channel = Literal["console", "telegram", "webhook"]


class SendReportParams(ToolParams):
    message: str = Field(description="Текст сообщения (markdown разрешён)")
    channel: Channel = Field(default="console", description="Куда доставить результат")
    target: str = Field(default="", description="chat_id для Telegram или URL вебхука")
    table_markdown: str = Field(default="", description="Таблица, которую надо приложить")


async def _send_telegram(ctx: ToolContext, target: str, text: str) -> dict[str, Any]:
    import httpx

    token = getattr(ctx.settings, "telegram_bot_token", "")
    if not token:
        raise ToolError("не задан AAP_TELEGRAM_BOT_TOKEN — отправить в Telegram нечем")
    chat_id = target or getattr(ctx.settings, "telegram_chat_id", "")
    if not chat_id:
        raise ToolError("не задан chat_id: укажите target или AAP_TELEGRAM_CHAT_ID")

    async with httpx.AsyncClient(timeout=ctx.http_timeout) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        )
        if response.status_code >= 400:
            raise ToolError(f"Telegram ответил {response.status_code}: {response.text[:200]}")
    return {"delivered": True, "channel": "telegram", "target": str(chat_id)}


async def _send_webhook(ctx: ToolContext, target: str, payload: dict[str, Any]) -> dict[str, Any]:
    import httpx

    if not target:
        raise ToolError("для канала webhook нужен target — URL получателя")
    async with httpx.AsyncClient(timeout=ctx.http_timeout) as client:
        response = await client.post(target, json=payload)
        if response.status_code >= 400:
            raise ToolError(f"вебхук ответил {response.status_code}")
    return {"delivered": True, "channel": "webhook", "target": target}


async def send_report(ctx: ToolContext, params: SendReportParams) -> dict[str, Any]:
    payload = {
        "message": params.message,
        "table_markdown": params.table_markdown,
        "source": "ai-automation-platform",
    }

    if ctx.demo_mode and params.channel != "console":
        return {
            "delivered": False,
            "simulated": True,
            "channel": params.channel,
            "target": params.target,
            "payload_preview": payload["message"][:400],
            "note": "демо-режим: наружу ничего не отправлено, доставка сымитирована",
        }

    if params.channel == "console":
        text = params.message + ("\n\n" + params.table_markdown if params.table_markdown else "")
        print("─" * 60)
        print("[send_report → console]")
        print(text)
        print("─" * 60)
        return {"delivered": True, "channel": "console", "simulated": False}
    if params.channel == "telegram":
        result = await _send_telegram(ctx, params.target, params.message)
    else:
        result = await _send_webhook(ctx, params.target, payload)
    return {**result, "simulated": False}


SEND_REPORT = Tool(
    name="send_report",
    description=(
        "Отправить готовый результат пользователю: в консоль, в Telegram или "
        "вебхуком. Требует подтверждения человека, потому что сообщение уходит наружу."
    ),
    params=SendReportParams,
    run=send_report,
    requires_approval=True,
    tags=("delivery",),
)
