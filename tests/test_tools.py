"""Инструменты: демо-каталог, ранжирование, таблицы, браузер, доставка, файлы."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.core.tools.base import ToolError, build_registry
from app.core.tools.browser import html_to_text
from app.core.tools.demo_catalog import demo_search
from app.core.tools.marketplace import SearchParams, search_products
from app.core.tools.notify import SendReportParams, send_report
from app.core.tools.ranking import RankParams, rank_products, score_item
from app.core.tools.storage import SaveArtifactParams, safe_filename, save_artifact
from app.core.tools.tables import TableParams, build_table, format_money, render_markdown, to_csv
from tests.expect import raises
from tests.helpers import context


# ── Демо-каталог ────────────────────────────────────────────────────────────
def test_demo_search_is_deterministic() -> None:
    first = demo_search("наушники", limit=5)
    second = demo_search("наушники", limit=5)
    assert first == second
    assert len(first) == 5


def test_demo_search_respects_max_price() -> None:
    ctx = context()
    result = asyncio.run(search_products(ctx, SearchParams(query="наушники", limit=20, max_price=3000)))
    assert result["source"] == "demo"
    assert all(item["price"] <= 3000 for item in result["items"])


def test_demo_search_marks_demo_source() -> None:
    result = asyncio.run(search_products(context(), SearchParams(query="рюкзак", limit=3)))
    assert result["source"] == "demo" and "демо" in result["note"]


def test_search_normalizes_missing_fields() -> None:
    from app.core.tools.marketplace import _normalize

    items = _normalize([{"name": "Товар", "min_price": "1490.5"}, {"title": "Другой", "price": None}])
    assert items[0]["price"] == 1490 and items[1]["price"] == 0
    assert items[1]["marketplace"] == "unknown"


# ── Ранжирование ────────────────────────────────────────────────────────────
def test_score_item_prefers_cheaper_and_more_reviewed() -> None:
    cheap, _ = score_item({"price": 500, "rating": 4.8, "reviews": 2000}, 500, 5000)
    pricey, _ = score_item({"price": 5000, "rating": 4.8, "reviews": 2000}, 500, 5000)
    assert cheap > pricey


def test_score_item_gives_bonus_for_requirements() -> None:
    plain, _ = score_item({"price": 1000, "rating": 4.5, "reviews": 100, "title": "Наушники"}, 1000, 2000)
    matched, explain = score_item(
        {"price": 1000, "rating": 4.5, "reviews": 100, "title": "Наушники с шумоподавлением"},
        1000,
        2000,
        prefer=["шумоподавление"],
    )
    assert matched > plain
    assert explain["requirement_hits"] == ["шумоподавление"]


def test_rank_products_sorts_and_limits() -> None:
    items = [
        {"id": "1", "price": 5000, "rating": 3.5, "reviews": 10},
        {"id": "2", "price": 1000, "rating": 5.0, "reviews": 3000},
        {"id": "3", "price": 2000, "rating": 4.5, "reviews": 500},
    ]
    result = asyncio.run(rank_products(context(), RankParams(items=items, top=2)))
    assert [i["id"] for i in result["top"]] == ["2", "3"]
    assert result["top"][0]["score"] >= result["top"][1]["score"]
    assert "why" in result["top"][0]


def test_rank_products_requires_at_least_one_item() -> None:
    """Пустой список отсекается ещё на схеме — до запуска инструмента."""
    from pydantic import ValidationError

    with raises(ValidationError, "at least 1 item"):
        RankParams(items=[])


# ── Таблицы ─────────────────────────────────────────────────────────────────
def test_format_money_ru_style() -> None:
    assert format_money(4990) == "4 990 ₽"
    assert format_money("12345.6") == "12 346 ₽"
    assert format_money(None) == "—"
    assert format_money("не число") == "не число"


def test_render_markdown_escapes_pipes() -> None:
    table = render_markdown([{"title": "Кофе | зерно", "price": 990}], ["title", "price"], "Топ")
    assert "\\|" in table and "990 ₽" in table and table.startswith("**Топ**")


def test_render_markdown_without_rows() -> None:
    assert render_markdown([]) == "_(нет данных)_"


def test_build_table_returns_markdown_and_csv() -> None:
    result = asyncio.run(
        build_table(context(), TableParams(rows=[{"title": "A", "price": 1500}], title="Тест"))
    )
    assert result["row_count"] == 1
    assert result["csv"].startswith("title,price")
    assert "1 500 ₽" in result["markdown"]


def test_to_csv_uses_requested_columns() -> None:
    csv_text = to_csv([{"a": 1, "b": 2}], ["b"])
    assert csv_text.strip().splitlines() == ["b", "2"]


# ── Браузер ─────────────────────────────────────────────────────────────────
def test_html_to_text_drops_scripts_and_markup() -> None:
    html = "<html><head><style>p{color:red}</style><script>var x=1</script></head>" \
           "<body><h1>Товар</h1><p>Цена 1 000 ₽</p><br><li>Пункт</li></body></html>"
    text = html_to_text(html)
    assert "var x" not in text and "color:red" not in text
    assert "Товар" in text and "1 000 ₽" in text and "Пункт" in text


def test_open_page_in_demo_mode_does_not_touch_network() -> None:
    from app.core.tools.browser import OpenPageParams, open_page

    result = asyncio.run(open_page(context(), OpenPageParams(url="https://example.invalid/x")))
    assert result["channel"] == "demo" and "сеть не использовалась" in result["note"]


def test_open_page_rejects_relative_url() -> None:
    from app.core.tools.browser import OpenPageParams, open_page

    with raises(ToolError, "полный URL"):
        asyncio.run(open_page(context(), OpenPageParams(url="/local/path")))


# ── Доставка ────────────────────────────────────────────────────────────────
def test_send_report_in_demo_simulates_external_delivery() -> None:
    result = asyncio.run(
        send_report(
            context(),
            SendReportParams(message="готово", channel="telegram", target="12345"),
        )
    )
    assert result["delivered"] is False and result["simulated"] is True
    assert "не отправлено" in result["note"]


def test_send_report_console_always_works() -> None:
    result = asyncio.run(send_report(context(), SendReportParams(message="привет", channel="console")))
    assert result["delivered"] is True and result["simulated"] is False


def test_send_report_without_token_reports_the_reason() -> None:
    ctx = context(demo=False, telegram_bot_token="")
    with raises(ToolError, "AAP_TELEGRAM_BOT_TOKEN"):
        asyncio.run(send_report(ctx, SendReportParams(message="привет", channel="telegram", target="1")))


# ── Артефакты ───────────────────────────────────────────────────────────────
def test_safe_filename_strips_traversal() -> None:
    assert safe_filename("../../etc/passwd", ".csv") == "etc_passwd.csv"
    with raises(ToolError, "расширение"):
        safe_filename("script.sh")


def test_save_artifact_writes_inside_directory() -> None:
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        ctx = context(directory)
        result = asyncio.run(
            save_artifact(ctx, SaveArtifactParams(name="выгрузка", content="a,b\n1,2\n", suffix=".csv"))
        )
        saved = Path(result["path"])
        assert saved.is_file()
        assert saved.parent == directory.resolve()
        assert saved.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_registry_contains_all_tools_with_schemas() -> None:
    registry = build_registry()
    assert registry.names() == [
        "build_table",
        "open_page",
        "rank_products",
        "save_artifact",
        "search_products",
        "send_report",
    ]
    schema = registry.get("search_products").json_schema()
    assert schema["type"] == "function"
    assert "query" in schema["function"]["parameters"]["properties"]


def test_registry_rejects_duplicate_registration() -> None:
    registry = build_registry()
    tool = registry.get("build_table")
    with raises(ValueError, "уже зарегистрирован"):
        registry.register(tool)
