"""Форматирование значений для отчётов.

Живёт отдельно от инструментов, чтобы и таблицы, и подстановки в тексте отчёта
(см. `agent/refs.py`) показывали числа одинаково: одно место — один формат.
"""

from __future__ import annotations

from typing import Any


def stringify(value: Any) -> str:
    """Человекочитаемый текст вместо `repr`: в отчёт попадают списки и словари."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(stringify(v) for v in value) if value else "—"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {stringify(v)}" for k, v in value.items()) or "—"
    return str(value).strip() or "—"


def format_money(value: Any) -> str:
    """Цена в рублях: разделители тысяч и символ валюты."""
    if value is None:
        return "—"
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,}".replace(",", " ") + " ₽"
