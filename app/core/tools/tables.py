"""Инструмент построения таблицы.

Рендер таблицы тоже отдан коду, а не модели: LLM выбирает, *что* показать,
а формат (выравнивание чисел, разделители тысяч, экранирование палочек,
CSV для Excel) собирает функция. Так таблица не «поедет» от одного лишнего
символа в названии товара.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from pydantic import Field

from app.core.formatting import format_money
from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams

MONEY_COLUMNS = {"price", "цена", "total", "сумма"}
DEFAULT_COLUMNS = ("title", "price", "rating", "reviews", "marketplace")


def format_value(value: Any, column: str) -> str:
    if value is None:
        return "—"
    if column in MONEY_COLUMNS:
        return format_money(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
    title: str = "",
) -> str:
    """Markdown-таблица. Колонки берутся из строк, если не заданы."""
    if not rows:
        return "_(нет данных)_"
    columns = columns or [c for c in DEFAULT_COLUMNS if any(c in r for r in rows)]
    columns = columns or list(rows[0].keys())
    headers = {"title": "Товар", "price": "Цена", "rating": "Рейтинг",
               "reviews": "Отзывы", "marketplace": "Площадка", "score": "Оценка", "url": "Ссылка"}
    head = "| " + " | ".join(headers.get(c, c) for c in columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(_escape(format_value(row.get(c), c)) for c in columns) + " |"
        for row in rows
    ]
    table = "\n".join([head, sep, *body])
    return f"**{title}**\n\n{table}" if title else table


def to_csv(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return ""
    columns = columns or list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c) for c in columns})
    return buffer.getvalue()


class TableParams(ToolParams):
    rows: list[dict[str, Any]] = Field(min_length=1, description="Строки таблицы")
    columns: list[str] = Field(default_factory=list, description="Колонки в нужном порядке")
    title: str = Field(default="", description="Заголовок таблицы")


async def build_table(ctx: ToolContext, params: TableParams) -> dict[str, Any]:
    if not params.rows:
        raise ToolError("таблица без строк: нечего показывать")
    columns = params.columns or None
    markdown = render_markdown(params.rows, columns, params.title)
    return {
        "markdown": markdown,
        "csv": to_csv(params.rows, columns),
        "columns": columns or list(params.rows[0].keys()),
        "rows": params.rows,
        "row_count": len(params.rows),
    }


BUILD_TABLE = Tool(
    name="build_table",
    description=(
        "Собрать таблицу из строк: markdown для сообщения и CSV для выгрузки. "
        "Цены форматируются автоматически."
    ),
    params=TableParams,
    run=build_table,
    tags=("logic", "report"),
)
