"""Сквозной демо-прогон: то, что обещает README, проверяется одной командой.

Этот тест — самая важная проверка набора: он проходит весь путь (задача → план →
инструменты → таблица → доставка → запись в SQLite) без ключей, без сети и без
внешних баз. Если он падает, обещание «демо работает без ключей» перестало быть
правдой.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from app.demo import DEFAULT_GOAL, run

GOAL = "Найди 20 товаров категории наушники до 5000 ₽, выбери 5 лучших, составь таблицу и отправь мне отчёт"


def _database() -> str:
    return "sqlite:///" + str(Path(tempfile.mkdtemp()) / "demo.db")


def test_demo_goal_is_the_one_from_the_readme() -> None:
    assert "найди" in DEFAULT_GOAL.lower() and "5000" in DEFAULT_GOAL


def test_full_offline_run() -> None:
    report = asyncio.run(run(GOAL, database=_database(), auto_approve=True, verbose=False))

    assert report["status"] == "done"
    assert report["plan_source"] == "deterministic"
    assert [step["tool"] for step in report["plan"]] == [
        "search_products",
        "rank_products",
        "build_table",
        "send_report",
    ]
    assert len(report["rows"]) == 5
    assert report["table_markdown"].startswith("**Топ-5")
    assert report["approvals"] == 1  # отправка наружу прошла через подтверждение
    assert report["skipped"] == []


def test_report_is_honest_about_demo_data() -> None:
    report = asyncio.run(run(GOAL, database=_database(), auto_approve=True, verbose=False))
    assert "демонстрационный каталог" in report["summary"]
    assert len(report["events"]) >= 8


def test_json_output_is_pure_json() -> None:
    """`--json` печатает только JSON: вывод инструментов уходит в console_output.

    Регрессия, найденная CI: рамка консольной доставки попадала в stdout и
    ломала разбор отчёта.
    """
    report = asyncio.run(run(GOAL, database=_database(), auto_approve=True, verbose=False))
    payload = json.dumps(report, ensure_ascii=False)
    restored = json.loads(payload)
    assert restored["task_id"] == report["task_id"]
    assert "send_report" in restored["console_output"]
    for line in restored["console_output"].splitlines():
        assert not line.startswith("{"), "консольный вывод не должен быть JSON-ом"


def test_demo_can_stop_at_approval() -> None:
    report = asyncio.run(run(GOAL, database=_database(), auto_approve=False, verbose=False))
    assert report["status"] == "waiting_approval"
    assert report["approvals"] == 0
    assert report["summary"] == ""  # результата нет, пока человек не подтвердил
    assert report["delivery"] == {}


def test_demo_respects_goal_constraints() -> None:
    report = asyncio.run(
        run(
            "Найди 12 товаров категории кофемашина до 20000 ₽, выбери 3 лучших и сохрани csv",
            database=_database(),
            auto_approve=True,
            verbose=False,
        )
    )
    assert report["status"] == "done"
    assert len(report["rows"]) == 3
    assert all(row["price"] <= 20000 for row in report["rows"])
    assert report["artifacts"], "CSV должен быть сохранён"
    search_step = next(step for step in report["steps"] if step["tool"] == "search_products")
    assert search_step["output"]["count"] == 12
