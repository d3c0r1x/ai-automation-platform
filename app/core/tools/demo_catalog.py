"""Детерминированный демо-каталог товаров.

Нужен для двух вещей:

1. **Демо без ключей.** Платформу можно показать целиком (веб-форма → задача →
   план → шаги → таблица), не имея доступа ни к одному маркетплейсу.
2. **Проверяемые тесты.** Каталог не случайный: он собирается из seed, равного
   хешу запроса, поэтому одинаковый запрос всегда даёт одинаковую выдачу.

Демо-данные никогда не подменяют реальный ответ молча: в выводе инструмента
всегда есть поле `source`, и оно уходит и в дашборд, и в финальный отчёт.
"""

from __future__ import annotations

import hashlib
from typing import Any

CATALOG: dict[str, dict[str, Any]] = {
    "наушники": {
        "brands": ["Aurora", "Klang", "Nordbell", "Vostok Audio", "SonicLine"],
        "models": ["Air Pro", "Studio 3", "Bass Mini", "TWS Lite", "Over-Ear X"],
        "price": (1490, 8990),
        "specs": ["шумоподавление", "Bluetooth 5.3", "до 30 часов", "USB-C", "влагозащита IPX4"],
    },
    "кроссовки": {
        "brands": ["Runway", "Stridex", "Kanto", "Freeform"],
        "models": ["City Run", "Trail 2", "Everyday Knit", "Court Low"],
        "price": (1990, 11900),
        "specs": ["дышащий верх", "амортизация", "белая подошва", "для асфальта"],
    },
    "кофемашина": {
        "brands": ["Barista Lab", "Crema", "Mokka", "Delonghi-style"],
        "models": ["Compact S", "Auto Milk", "Pro 500", "Mini Bar"],
        "price": (6900, 54900),
        "specs": ["капучинатор", "19 бар", "автопромывка", "встроенная кофемолка"],
    },
    "рюкзак": {
        "brands": ["Pathfinder", "Urban Pack", "Terra", "City Bear"],
        "models": ["Day 20L", "Laptop 15", "Trek 30L", "Slim City"],
        "price": (1290, 7990),
        "specs": ["отделение для ноутбука", "водоотталкивающая ткань", "USB-порт"],
    },
    "пылесос": {
        "brands": ["Cyclone", "HomeWay", "Vento", "Cleanix"],
        "models": ["Robot S1", "Handy 2", "Vertical Max", "Wet&Dry"],
        "price": (3490, 39900),
        "specs": ["аккумулятор 2500 мАч", "HEPA-фильтр", "влажная уборка"],
    },
}

DEFAULT_CATEGORY = {
    "brands": ["Generic", "Standard", "Basic", "Universal", "Classic"],
    "models": ["Model 1", "Model 2", "Model 3", "Plus", "Max"],
    "price": (990, 9900),
    "specs": ["гарантия 1 год", "экономный расход", "компактный корпус"],
}

MARKETPLACES = ("ozon", "wildberries", "yandex_market")


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


class _Rng:
    """Линейный конгруэнтный генератор: детерминированный и без зависимостей.

    `random.Random(seed)` тоже подошёл бы, но свой генератор не меняет числа
    между версиями Python — а значит, демо-выдача и тесты воспроизводимы всегда.
    """

    def __init__(self, seed: int) -> None:
        self._state = seed % (2**31 - 1) or 1

    def next(self) -> float:
        self._state = (1103515245 * self._state + 12345) % (2**31)
        return self._state / (2**31)

    def between(self, low: int, high: int) -> int:
        return low + int(self.next() * (high - low))

    def pick(self, items: list[str]) -> str:
        return items[int(self.next() * len(items)) % len(items)]


def category_for(query: str) -> str:
    query = (query or "").lower()
    for name in CATALOG:
        if name in query:
            return name
    return ""


def demo_search(query: str, limit: int = 20, max_price: int | None = None) -> list[dict[str, Any]]:
    """Собрать до `limit` товаров по запросу. Результат стабилен для запроса."""
    name = category_for(query) or "прочее"
    spec = CATALOG.get(name, DEFAULT_CATEGORY)
    rng = _Rng(_seed(name, str(max_price or 0)))
    low, high = spec["price"]
    if max_price:
        high = min(high, int(max_price))
        low = min(low, max(0, high - 500))
    if high <= low:
        high = low + 500

    items: list[dict[str, Any]] = []
    for index in range(limit):
        price = rng.between(low, high)
        rating = round(3.5 + rng.next() * 1.5, 1)
        items.append(
            {
                "id": f"{name}-{index + 1:02d}",
                "title": f"{rng.pick(spec['brands'])} {rng.pick(spec['models'])}",
                "category": name,
                "price": price,
                "rating": rating,
                "reviews": rng.between(12, 4800),
                "marketplace": MARKETPLACES[index % len(MARKETPLACES)],
                "url": f"https://example.invalid/{name}/{index + 1}",
                "specs": [rng.pick(spec["specs"]) for _ in range(2)],
            }
        )
    return items
