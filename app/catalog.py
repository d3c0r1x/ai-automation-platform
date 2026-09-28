CATALOG = [
    {"id": 1, "name": "Wireless headset", "price": 3490, "rating": 4.7},
    {"id": 2, "name": "USB-C hub", "price": 2190, "rating": 4.8},
    {"id": 3, "name": "Mechanical keyboard", "price": 4990, "rating": 4.6},
    {"id": 4, "name": "Web camera", "price": 2890, "rating": 4.5},
    {"id": 5, "name": "Desk lamp", "price": 1790, "rating": 4.9},
    {"id": 6, "name": "Laptop stand", "price": 2390, "rating": 4.7},
]


def search_catalog(query: str) -> list[dict]:
    return CATALOG.copy()


def rank_products(products: list[dict], limit: int = 5) -> list[dict]:
    return sorted(products, key=lambda p: (-p["rating"], p["price"]))[:limit]
