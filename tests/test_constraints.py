"""Разбор цели: числа и требования берутся из текста, а не из «понимания»."""

from __future__ import annotations

from app.core.agent.constraints import parse_goal


def test_example_from_readme() -> None:
    c = parse_goal(
        "Найди 20 товаров категории наушники до 5000 ₽, сравни их, "
        "выбери 5 лучших по цене и отзывам, составь таблицу и отправь мне отчёт"
    )
    assert c.query.lower() == "наушники"
    assert c.limit == 20
    assert c.top == 5
    assert c.max_price == 5000
    assert c.channel == "console"


def test_max_price_with_spaces_and_rubles() -> None:
    assert parse_goal("кроссовки до 12 000 рублей, топ 3").max_price == 12000
    assert parse_goal("подбери рюкзак не дороже 4 500 р.").max_price == 4500


def test_top_never_exceeds_limit() -> None:
    c = parse_goal("найди 3 товара категории пылесос, выбери 10 лучших")
    assert c.limit == 3
    assert c.top == 3


def test_requirements_from_lexicon() -> None:
    c = parse_goal("найди кофемашину с капучинатором до 30000 ₽")
    assert "капучинатор" in c.prefer


def test_quoted_requirements_are_kept() -> None:
    c = parse_goal("найди наушники с «шумоподавление» до 8000 ₽")
    assert any("шумоподавление" in p for p in c.prefer)


def test_telegram_target_and_csv() -> None:
    c = parse_goal("найди 10 товаров категории рюкзак и отправь в телеграм @my_chat, сохрани csv")
    assert c.channel == "telegram"
    assert c.target == "@my_chat"
    assert c.wants_csv


def test_browser_request() -> None:
    assert parse_goal("найди наушники и посмотри страницы товаров").wants_browser


def test_query_is_not_empty_even_for_odd_phrasing() -> None:
    c = parse_goal("хочу подобрать что-то недорогое")
    assert c.query.strip()
    assert c.limit >= 1 and c.top >= 1


def test_describe_is_human_readable() -> None:
    text = parse_goal("найди 5 товаров категории наушники до 3000 ₽").describe()
    assert "наушники" in text and "3 000" not in text  # без форматирования, но с числами
    assert "3000" in text
