"""Инструмент работы с браузером (Playwright) и запасным HTTP-каналом.

Зачем браузер, если есть API: часть страниц отдаёт данные только после
исполнения JavaScript. Поэтому инструмент пробует Playwright, а если он не
установлен или браузер не поднялся — честно уходит на обычный HTTP GET и
**сообщает об этом в `channel`**. Молчаливой подмены нет: вызывающий код и
дашборд видят, каким каналом получены данные.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import Field

from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams

_SCRIPT = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def html_to_text(html: str, limit: int = 4000) -> str:
    """Грубое, но предсказуемое преобразование HTML в текст (без bs4)."""
    text = _SCRIPT.sub(" ", html or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|h\d)>", "\n", text, flags=re.IGNORECASE)
    text = _TAGS.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = _SPACES.sub(" ", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()[:limit]


def _demo_page(url: str) -> dict[str, Any]:
    host = urlparse(url).netloc or "example.invalid"
    return {
        "url": url,
        "title": f"Страница товара на {host}",
        "text": (
            "Демо-страница: браузер не запускался, внешних запросов не было.\n"
            "В реальном режиме здесь HTML, отрендеренный Playwright после JS.\n"
            "Характеристики: доставка 2 дня, возврат 14 дней, гарантия 1 год."
        ),
    }


async def _via_playwright(url: str, timeout_s: int) -> tuple[str, str]:  # pragma: no cover
    from playwright.async_api import async_playwright  # lazy: необязательная зависимость

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
            html = await page.content()
            title = await page.title()
        finally:
            await browser.close()
    return html, title


async def _via_http(url: str, timeout_s: int) -> tuple[str, str]:
    import httpx

    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "ai-automation-platform/1.0"})
        if response.status_code >= 400:
            raise ToolError(f"страница ответила {response.status_code}")
        return response.text, ""


class OpenPageParams(ToolParams):
    url: str = Field(description="Адрес страницы")
    limit: int = Field(default=4000, ge=200, le=20000, description="Лимит символов текста")


async def open_page(ctx: ToolContext, params: OpenPageParams) -> dict[str, Any]:
    if not params.url.startswith(("http://", "https://")):
        raise ToolError("нужен полный URL, начинающийся с http:// или https://")

    if ctx.demo_mode:
        return {**_demo_page(params.url), "channel": "demo", "note": "демо-режим: сеть не использовалась"}

    browser_allowed = bool(getattr(ctx.settings, "browser_enabled", True))
    if browser_allowed:
        try:
            html, title = await _via_playwright(params.url, ctx.http_timeout)
            return {
                "url": params.url,
                "title": title or params.url,
                "text": html_to_text(html, params.limit),
                "channel": "playwright",
            }
        except ImportError:
            note = "Playwright не установлен — использован HTTP-канал без JS"
        except Exception as exc:  # причину надо видеть, а не глотать
            note = f"Playwright не смог открыть страницу ({type(exc).__name__}) — использован HTTP-канал"
    else:
        note = "браузер отключён настройкой AAP_BROWSER_ENABLED=0"

    html, title = await _via_http(params.url, ctx.http_timeout)
    return {
        "url": params.url,
        "title": title or params.url,
        "text": html_to_text(html, params.limit),
        "channel": "http",
        "note": note,
    }


OPEN_PAGE = Tool(
    name="open_page",
    description=(
        "Открыть страницу товара и получить её текст: сначала через браузер "
        "(Playwright, страницы с JavaScript), при недоступности — через HTTP."
    ),
    params=OpenPageParams,
    run=open_page,
    tags=("browser",),
)
