"""Инструмент отбора лучших товаров.

Ранжирование **детерминированное и объяснимое**, а не «на усмотрение модели».
Причина простая: подборка из 5 товаров — то, что человек увидит и проверит, и
«модель так решила» здесь неуместно. LLM выбирает *что сделать* (какие шаги,
какие требования важны), а числа считает код.

Итоговый скор — взвешенная сумма трёх нормализованных частей:

| Часть | Вес по умолчанию | Смысл |
|---|---|---|
| цена | 0.45 | дешевле — лучше, но не в абсолютных рублях, а относительно выборки |
| рейтинг | 0.30 | 3.0…5.0 растягивается на 0…1 |
| отзывы | 0.25 | логарифм: 20 отзывов и 2000 отличаются, а 2000 и 4000 почти нет |

Плюс надбавка за совпадение с требованиями из запроса («шумоподавление»,
«влажная уборка») — и каждое решение видно в `why`, а не спрятано в скор.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field

from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams

DEFAULT_WEIGHTS = {"price": 0.45, "rating": 0.30, "reviews": 0.25}
REQUIREMENT_BONUS = 0.12
SPREAD = 0.10  # столько «эластичности» перед финальной нормировкой в 0…1


def _price_score(price: int, low: int, high: int) -> float:
    if high <= low:
        return 1.0
    return 1.0 - (price - low) / (high - low)


def _rating_score(rating: float) -> float:
    return min(1.0, max(0.0, (rating - 3.0) / 2.0))


def _reviews_score(reviews: int) -> float:
    if reviews <= 0:
        return 0.0
    return min(1.0, math.log10(reviews) / 3.5)


def _requirement_hits(item: dict[str, Any], prefer: list[str]) -> list[str]:
    haystack = " ".join(
        [str(item.get("title", "")), " ".join(str(s) for s in item.get("specs", []))]
    ).lower()
    return [p for p in prefer if p and p.lower() in haystack]


def score_item(
    item: dict[str, Any],
    low: int,
    high: int,
    weights: dict[str, float] | None = None,
    prefer: list[str] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Вернуть (скор 0…1, объяснение). Функция чистая — её и проверяют тесты."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    total = sum(weights.values()) or 1.0

    parts = {
        "price": _price_score(int(item.get("price") or 0), low, high),
        "rating": _rating_score(float(item.get("rating") or 0)),
        "reviews": _reviews_score(int(item.get("reviews") or 0)),
    }
    base = sum(parts[key] * weights[key] for key in parts) / total
    hits = _requirement_hits(item, prefer or [])
    score = min(1.0, base + REQUIREMENT_BONUS * len(hits))

    why = " ".join(
        [
            f"цена {parts['price']:.2f}×{weights['price']:.2f}",
            f"рейтинг {parts['rating']:.2f}×{weights['rating']:.2f}",
            f"отзывы {parts['reviews']:.2f}×{weights['reviews']:.2f}",
        ]
    )
    if hits:
        why += " + требования: " + ", ".join(hits)
    return round(score, 4), {"why": why, "parts": parts, "requirement_hits": hits}


class RankParams(ToolParams):
    items: list[dict[str, Any]] = Field(min_length=1, description="Товары из search_products")
    top: int = Field(default=5, ge=1, le=50, description="Сколько лучших оставить")
    prefer: list[str] = Field(default_factory=list, description="Требования из запроса")
    weights: dict[str, float] | None = Field(default=None, description="Веса: price/rating/reviews")


async def rank_products(ctx: ToolContext, params: RankParams) -> dict[str, Any]:
    if not params.items:
        raise ToolError("нечего ранжировать: список товаров пуст")
    prices = [int(i.get("price") or 0) for i in params.items]
    low, high = min(prices), max(prices)

    ranked: list[dict[str, Any]] = []
    for item in params.items:
        score, explain = score_item(item, low, high, params.weights, params.prefer)
        ranked.append({**item, "score": score, "why": explain["why"], "hits": explain["requirement_hits"]})
    ranked.sort(key=lambda i: (-i["score"], i["price"]))

    return {
        "ranked": ranked,
        "top": ranked[: params.top],
        "total": len(ranked),
        "price_range": {"min": low, "max": high},
        "requirements": params.prefer,
    }


RANK_PRODUCTS = Tool(
    name="rank_products",
    description=(
        "Отобрать лучшие товары из выборки по цене, рейтингу и числу отзывов "
        "с учётом требований из запроса. Возвращает отсортированный список "
        "с оценкой и пояснением для каждого товара."
    ),
    params=RankParams,
    run=rank_products,
    tags=("logic", "marketplace"),
)
