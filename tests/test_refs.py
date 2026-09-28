"""Ссылки между шагами: разрешение, преобразования, порядок выполнения."""

from __future__ import annotations

from tests.expect import raises

from app.core.agent.refs import (
    RefError,
    alias_map,
    normalize_references,
    get_path,
    get_path_with_transform,
    ordered_steps,
    referenced_steps,
    resolve_args,
    stringify,
)
from app.core.models import PlanStep

OUTPUTS = {
    "search": {"items": [{"title": "A", "price": 1990}, {"title": "B", "price": 500}], "count": 2},
    "rank": {"top": [{"id": "x", "url": "https://example.invalid/x", "price": 1990}], "requirements": []},
    "table": {"markdown": "| a |", "csv": "a\n1\n"},
}


def test_get_path_simple_and_nested() -> None:
    assert get_path(OUTPUTS, "search.count") == 2
    assert get_path(OUTPUTS, "search.items.0.title") == "A"
    assert get_path(OUTPUTS, "table.markdown") == "| a |"


def test_get_path_alternatives() -> None:
    assert get_path(OUTPUTS, "rank.top|items")[0]["id"] == "x"
    assert get_path(OUTPUTS, "search.top|items")[0]["title"] == "A"


def test_missing_step_reports_available() -> None:
    with raises(RefError, "нет результатов шага"):
        get_path(OUTPUTS, "nope.field")


def test_missing_field_raises() -> None:
    with raises(RefError):
        get_path(OUTPUTS, "search.unknown")


def test_index_out_of_range_raises() -> None:
    with raises(RefError):
        get_path(OUTPUTS, "search.items.9.title")


def test_money_transform() -> None:
    assert get_path_with_transform(OUTPUTS, "search.items.0.price|money") == "1 990 ₽"
    assert get_path_with_transform(OUTPUTS, "search.items.0.price") == 1990


def test_stringify_lists_and_empty() -> None:
    assert stringify(["a", "b"]) == "a, b"
    assert stringify([]) == "—"
    assert stringify(None) == "—"
    assert stringify(True) == "да"


def test_resolve_full_and_inline_references() -> None:
    args = {
        "rows": "$rank.top",
        "message": "Найдено {{search.count}} товаров: {{search.items}}",
        "nested": {"price": "$search.items.1.price"},
    }
    resolved = resolve_args(args, OUTPUTS)
    assert resolved["rows"] == OUTPUTS["rank"]["top"]
    assert resolved["message"].startswith("Найдено 2 товаров: ")
    assert "title: A" in resolved["message"]  # список товаров читаемо разворачивается в текст
    assert resolved["nested"]["price"] == 500


def test_resolve_with_money_in_inline() -> None:
    assert resolve_args({"m": "цена {{search.items.0.price|money}}"}, OUTPUTS)["m"] == "цена 1 990 ₽"


def test_referenced_steps_finds_both_forms() -> None:
    found = referenced_steps({"a": "$rank.top", "b": "x {{search.count}} y", "c": ["$table.csv"]})
    assert found == {"rank", "search", "table"}


def test_alias_map_understands_semantic_names() -> None:
    steps = [
        PlanStep(id="search_products", tool="search_products"),
        PlanStep(id="build_table", tool="build_table", args={"rows": "$search.items"}),
    ]
    normalized = normalize_references(steps)
    assert normalized[1].args["rows"] == "$search_products.items"
    assert {"search", "products", "table", "build"} <= set(alias_map(steps))


def test_unknown_alias_is_left_for_validation_to_reject() -> None:
    steps = [PlanStep(id="build_table", tool="build_table", args={"rows": "$nowhere.items"})]
    assert normalize_references(steps)[0].args["rows"] == "$nowhere.items"


def test_ordered_steps_respects_dependencies() -> None:
    steps = [
        PlanStep(id="c", tool="t", args={"x": "$b.y"}, depends_on=["b"]),
        PlanStep(id="b", tool="t", args={"x": "$a.y"}, depends_on=["a"]),
        PlanStep(id="a", tool="t"),
    ]
    assert [s.id for s in ordered_steps(steps)] == ["a", "b", "c"]


def test_ordered_steps_detects_cycle() -> None:
    steps = [
        PlanStep(id="a", tool="t", depends_on=["b"]),
        PlanStep(id="b", tool="t", depends_on=["a"]),
    ]
    with raises(RefError):
        ordered_steps(steps)
