"""Разбор цели на ограничения.

Нужен в двух местах:

1. **Детерминированный планировщик** — режим без ключа LLM, в котором
   платформа обязана работать (демо, CI, тесты).
2. **Проверка плана от LLM** — если модель предложила план, ограничения из
   запроса всё равно извлекаются кодом: числа берутся из текста, а не из
   «понимания» модели. Модель может ошибиться в цене, а регулярка — нет.

Разбор намеренно простой и предсказуемый: он покрыт тестами на русские
формулировки («найди 20 товаров до 5000 ₽, выбери 5 лучших»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMBER_WORDS = {
    "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}

# Требования, которые имеет смысл проверять по названию и характеристикам.
LEXICON = (
    "шумоподавление", "шумоподавлением", "bluetooth", "влагозащита", "влажная уборка",
    "капучинатор", "кофемолка", "для ноутбука", "амортизация", "лёгкий", "легкий",
    "беспроводные", "проводные", "для асфальта", "долгая работа", "быстрая зарядка",
)

MONEY = r"(?:₽|руб\w*|р\.)"
LIMIT_RE = re.compile(r"(?P<n>\d+)\s*(?:шт|товар\w*|позиц\w*|вариант\w*)?", re.IGNORECASE)
MAX_PRICE_RE = re.compile(rf"(?:до|не дороже|дешевле|максимум|в пределах)\s*(?P<n>[\d\s]{{2,9}})\s*{MONEY}", re.IGNORECASE)
TOP_RE = re.compile(r"(?:топ|лучши\w*|выбери|отбери)\D{0,12}?(?P<n>\d+)", re.IGNORECASE)
FIND_RE = re.compile(r"(?:найд|ищи|поищи|подбери)\D{0,12}?(?P<n>\d+)\s*(?:шт|товар\w*|позиц\w*|вариант\w*)", re.IGNORECASE)
CATEGORY_RE = re.compile(r"категории\s+(?P<name>[«\"']?[\w\s-]{3,40}?[»\"']?)(?=[,.]|$|\sдо|\sвыб|\sсравн|\sсостав)", re.IGNORECASE)

STOP_PHRASES = (
    "найди", "найти", "ищи", "поищи", "подбери", "сравни", "сравнить", "выбери",
    "отбери", "составь", "сделай", "отправь", "отправь мне", "мне", "пришли",
    "таблицу", "таблицей", "в таблицу", "список", "отчёт", "отчет", "в телеграм",
    "в тг", "по цене", "по цене и отзывам", "и отзывам", "лучших", "лучшие",
    "товаров", "товара", "товары", "самых", "потом", "затем", "и", "с", "по",
    "в csv", "csv", "выгрузи", "сохрани", "файл",
)


@dataclass
class GoalConstraints:
    query: str = ""
    limit: int = 20
    max_price: int | None = None
    top: int = 5
    prefer: list[str] = field(default_factory=list)
    channel: str = "console"
    target: str = ""
    wants_csv: bool = False
    wants_browser: bool = False
    raw: str = ""

    def describe(self) -> str:
        parts = [f"запрос «{self.query}»", f"до {self.limit} товаров", f"отобрать {self.top}"]
        if self.max_price:
            parts.append(f"цена не выше {self.max_price} ₽")
        if self.prefer:
            parts.append("требования: " + ", ".join(self.prefer))
        parts.append(f"доставка: {self.channel}")
        if self.wants_csv:
            parts.append("сохранить CSV")
        if self.wants_browser:
            parts.append("открыть страницы товаров")
        return "; ".join(parts)


def _to_int(raw: str) -> int | None:
    cleaned = re.sub(r"\s+", "", raw)
    try:
        return int(cleaned)
    except ValueError:
        return None


def _fallback_query(goal: str) -> str:
    text = goal.lower()
    for phrase in sorted(STOP_PHRASES, key=len, reverse=True):
        text = text.replace(phrase, " ")
    text = re.sub(rf"\d+\s*{MONEY}", " ", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    words = [w for w in text.split() if len(w) > 2]
    return " ".join(words[:6]).strip() or goal.strip()


def parse_goal(goal: str) -> GoalConstraints:
    """Разобрать цель на ограничения. Ничего не выдумывает: только то, что в тексте."""
    text = (goal or "").strip()
    lower = text.lower()
    constraints = GoalConstraints(raw=text)

    category = CATEGORY_RE.search(text)
    find = FIND_RE.search(text)
    if category:
        constraints.query = category.group("name").strip()
    elif find:
        before = text[: find.start()]
        constraints.query = _fallback_query(before) or _fallback_query(text)
    else:
        constraints.query = _fallback_query(text)

    if find:
        value = _to_int(find.group("n"))
        if value:
            constraints.limit = max(1, min(50, value))
    else:
        numbers = [int(m.group("n")) for m in LIMIT_RE.finditer(text) if int(m.group("n")) > 0]
        if numbers:
            constraints.limit = max(1, min(50, numbers[0]))

    max_price = MAX_PRICE_RE.search(text)
    if max_price:
        constraints.max_price = _to_int(max_price.group("n"))

    top = TOP_RE.search(text)
    if top:
        value = _to_int(top.group("n"))
        if value:
            constraints.top = max(1, min(constraints.limit, value))

    for word in LEXICON:
        if word in lower:
            base = word.rstrip("ом").rstrip("а")
            if base not in constraints.prefer:
                constraints.prefer.append(base)

    for quoted in re.findall(r"[«\"']([^»\"']{3,40})[»\"']", text):
        quoted = quoted.strip()
        if quoted and quoted not in constraints.prefer:
            constraints.prefer.append(quoted)

    if re.search(r"телеграм|в тг|@\w+", lower):
        constraints.channel = "telegram"
    if re.search(r"вебхук|webhook|http[s]?://\S+", lower):
        constraints.channel = "webhook"
    target = re.search(r"(@\w+|\d{6,})", text)
    if target and constraints.channel == "telegram":
        constraints.target = target.group(1)

    constraints.wants_csv = bool(re.search(r"\bcsv\b|выгруз|excel|сохрани", lower))
    constraints.wants_browser = bool(re.search(r"посмотри страниц|открой страниц|зайди на|сайт", lower))
    return constraints
