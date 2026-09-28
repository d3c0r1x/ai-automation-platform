"""Инструмент поиска товаров.

Один и тот же инструмент работает в двух каналах:

- **API** (если задан `AAP_MARKETPLACE_BASE_URL`) — обычный HTTP-вызов;
  контракт: `GET {base}/search?q=&limit=&max_price=` → `{"items": [...]}`.
- **Демо** — детерминированный каталог (`demo_catalog.py`), без сети.

Канал всегда указан в выводе (`source`), поэтому подмены реального ответа
демо-данными «молча» не бывает: это видно и в дашборде, и в итоговом отчёте.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import Field

from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams
from app.core.tools.demo_catalog import demo_search


class SearchParams(ToolParams):
    query: str = Field(description="Поисковый запрос или категория товара")
    limit: int = Field(default=20, ge=1, le=50, description="Сколько товаров вернуть")
    max_price: int | None = Field(default=None, ge=1, description="Максимальная цена, ₽")
    marketplace: str | None = Field(default=None, description="Ограничить одной площадкой")


async def _search_via_api(ctx: ToolContext, params: SearchParams) -> list[dict[str, Any]]:
    settings = ctx.settings
    base = getattr(settings, "marketplace_base_url", "")
    headers = {}
    key = getattr(settings, "marketplace_api_key", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    query = {"q": params.query, "limit": params.limit}
    if params.max_price:
        query["max_price"] = params.max_price
    if params.marketplace:
        query["marketplace"] = params.marketplace

    async with httpx.AsyncClient(timeout=ctx.http_timeout) as client:
        response = await client.get(f"{base.rstrip('/')}/search", params=query, headers=headers)
        if response.status_code >= 400:
            raise ToolError(f"маркетплейс ответил {response.status_code}: {response.text[:200]}")
        payload = response.json()

    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ToolError("неожиданный формат ответа маркетплейса: нет списка items")
    return items


def _normalize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Привести товары к общей схеме: дальше все шаги ждут одинаковые поля."""
    normalized = []
    for raw in items:
        price = raw.get("price") or raw.get("min_price") or 0
        try:
            price = int(float(price))
        except (TypeError, ValueError):
            price = 0
        normalized.append(
            {
                "id": str(raw.get("id") or raw.get("sku") or raw.get("url") or len(normalized)),
                "title": str(raw.get("title") or raw.get("name") or "без названия"),
                "price": price,
                "rating": float(raw.get("rating") or 0),
                "reviews": int(raw.get("reviews") or raw.get("feedbacks") or 0),
                "marketplace": str(raw.get("marketplace") or raw.get("shop") or "unknown"),
                "url": str(raw.get("url") or ""),
                "category": str(raw.get("category") or ""),
                "specs": [str(s) for s in (raw.get("specs") or [])],
            }
        )
    return normalized


async def search_products(ctx: ToolContext, params: SearchParams) -> dict[str, Any]:
    settings = ctx.settings
    use_api = bool(getattr(settings, "marketplace_base_url", "")) and not ctx.demo_mode

    if use_api:
        items = _normalize(await _search_via_api(ctx, params))
        source = "api"
        note = "данные из внешнего API маркетплейса"
    else:
        items = _normalize(
            demo_search(params.query, limit=params.limit, max_price=params.max_price)
        )
        source = "demo"
        note = "демо-каталог: воспроизводимые данные, внешние запросы не выполнялись"

    if params.marketplace:
        items = [i for i in items if i["marketplace"] == params.marketplace]
    if params.max_price:
        items = [i for i in items if 0 < i["price"] <= params.max_price]

    return {
        "items": items,
        "count": len(items),
        "source": source,
        "note": note,
        "query": params.query,
    }


SEARCH_PRODUCTS = Tool(
    name="search_products",
    description=(
        "Найти товары по запросу на маркетплейсах. Возвращает список с ценой, "
        "рейтингом, числом отзывов и ссылкой."
    ),
    params=SearchParams,
    run=search_products,
    tags=("api", "marketplace"),
)
