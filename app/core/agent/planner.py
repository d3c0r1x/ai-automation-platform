"""Планировщик: цель → план вызовов инструментов.

Две стратегии и один контракт:

1. **LLM** (tool calling) — когда настроены `AAP_LLM_BASE_URL` и ключ. Модель
   выбирает шаги, но её план проходит **валидацию до выполнения**: неизвестный
   инструмент, аргументы не по схеме, ссылка на несуществующий шаг или цикл —
   это отказ, а не «попробуем и посмотрим».
2. **Детерминированный план** — всегда работает: без ключа, в CI, в тестах.
   Ограничения берутся из текста запроса (`constraints.py`), шаги строятся из
   тех же инструментов.

Отказ модели — не авария: платформа продолжает работу по второй стратегии и
честно пишет в плане, кто его построил (`source`) и что именно отвергла
валидация (`notes`).
"""

from __future__ import annotations

from typing import Any

from app.core.agent.constraints import parse_goal
from app.core.agent.llm import LlmClient, LlmUnavailable, NullLlmClient
from app.core.agent.refs import RefError, normalize_references, ordered_steps
from app.core.models import Plan, PlanSource, PlanStep
from app.core.tools.base import ToolError, ToolRegistry

RESERVED_ARG_KEYS = ("reason", "depends_on", "optional", "id", "requires_approval")
DELIVERY_TOOLS = ("build_table", "send_report")


def _slug(text: str, fallback: str = "report") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text.lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return (cleaned[:48] or fallback).strip("_")


class Planner:
    def __init__(
        self,
        registry: ToolRegistry,
        llm: LlmClient | None = None,
        max_steps: int = 12,
    ) -> None:
        self._registry = registry
        self._llm = llm or NullLlmClient()
        self.max_steps = max(1, max_steps)

    async def plan(self, goal: str) -> Plan:
        notes: list[str] = []
        if getattr(self._llm, "available", False):
            try:
                calls, model_note = await self._llm.plan(goal, self._registry.schemas(), self.max_steps)
                plan = self._plan_from_calls(goal, calls, model_note)
                plan.steps = self._ensure_deliverable(plan)
                ordered_steps(plan.steps)
                return plan
            except (LlmUnavailable, RefError, ToolError, ValueError) as exc:
                notes.append(f"план от LLM отклонён: {exc}")
        return self._deterministic(goal, notes)

    # ── План от LLM ──────────────────────────────────────────────────────────
    def _plan_from_calls(self, goal: str, calls: list[Any], model_note: str) -> Plan:
        steps: list[PlanStep] = []
        problems: list[str] = []
        used_ids: dict[str, int] = {}
        for index, call in enumerate(calls[: self.max_steps], start=1):
            if call.name not in self._registry:
                problems.append(f"шаг {index}: неизвестный инструмент {call.name!r}")
                continue
            tool = self._registry.get(call.name)
            raw = dict(call.arguments or {})
            reason = str(raw.pop("reason", "") or "")
            depends_on = raw.pop("depends_on", []) or []
            optional = bool(raw.pop("optional", False))
            raw.pop("id", None)
            raw.pop("requires_approval", None)
            try:
                # Проверяем имена полей, обязательные поля и типы обычных значений.
                # Ссылки вида `$search.items` проверит исполнитель: сейчас их
                # значение ещё не существует.
                checked = self._registry.check_args(call.name, raw)
            except ToolError as exc:
                problems.append(f"шаг {index} ({call.name}): {exc}")
                continue
            # Идентификатор шага — имя инструмента (с суффиксом при повторе):
            # тогда ссылки вида `$search.items` совпадают со смыслом, который
            # вложила модель, и шаги видно в дашборде без расшифровки.
            used_ids[call.name] = used_ids.get(call.name, 0) + 1
            repeat = used_ids[call.name]
            step_id = call.name if repeat == 1 else f"{call.name}_{repeat}"
            steps.append(
                PlanStep(
                    id=step_id,
                    tool=call.name,
                    args=checked,
                    reason=reason or tool.description,
                    depends_on=list(depends_on),
                    requires_approval=tool.requires_approval,
                    optional=optional,
                )
            )

        if not steps:
            raise LlmUnavailable("ни один шаг модели не прошёл валидацию")
        if problems:
            raise ValueError("; ".join(problems))

        known = {s.id for s in steps}
        for step in steps:
            unknown = [d for d in step.depends_on if d not in known]
            if unknown:
                raise RefError(f"шаг {step.id} зависит от несуществующих шагов: {unknown}")

        steps = normalize_references(steps)
        return Plan(
            goal=goal,
            steps=steps,
            expected_output="Результат задачи: сводка, таблица и доставка пользователю",
            source=PlanSource.LLM,
            notes=model_note.strip()[:500],
        )

    # ── Детерминированный план ───────────────────────────────────────────────
    def _deterministic(self, goal: str, notes: list[str]) -> Plan:
        c = parse_goal(goal)
        steps: list[PlanStep] = [
            PlanStep(
                id="search",
                tool="search_products",
                args={"query": c.query, "limit": c.limit, "max_price": c.max_price},
                reason=f"собрать до {c.limit} товаров по запросу «{c.query}»",
            ),
            PlanStep(
                id="rank",
                tool="rank_products",
                args={"items": "$search.items", "top": c.top, "prefer": c.prefer},
                reason="отобрать лучшие по цене, рейтингу и числу отзывов с учётом требований",
                depends_on=["search"],
            ),
            PlanStep(
                id="table",
                tool="build_table",
                args={
                    "rows": "$rank.top",
                    "columns": ["title", "price", "rating", "reviews", "marketplace", "score"],
                    "title": f"Топ-{c.top}: лучшие варианты",
                },
                reason="собрать таблицу для человека",
                depends_on=["rank"],
            ),
        ]
        if c.wants_browser:
            steps.append(
                PlanStep(
                    id="page",
                    tool="open_page",
                    args={"url": "$rank.top.0.url"},
                    reason="открыть страницу лучшего товара и получить подробности",
                    depends_on=["rank"],
                    optional=True,
                )
            )
        if c.wants_csv:
            steps.append(
                PlanStep(
                    id="csv",
                    tool="save_artifact",
                    args={"name": f"{_slug(c.query, 'items')}.csv", "content": "$table.csv", "suffix": ".csv"},
                    reason="сохранить выгрузку CSV",
                    depends_on=["table"],
                )
            )
        steps.append(
            PlanStep(
                id="report",
                tool="send_report",
                args={
                    # Двойные фигурные скобки — синтаксис ссылки, а не формат
                    # сообщения, поэтому строка собирается без f-string.
                    "message": "\n".join(
                        [
                            "Найдено {{search.count}} товаров, отобрано " + str(c.top) + " лучших.",
                            "Требования: {{rank.requirements}}",
                            "Площадка: {{rank.top.0.marketplace}}, лучшая цена: {{rank.top.0.price|money}}",
                        ]
                    ),
                    "channel": c.channel,
                    "target": c.target,
                    "table_markdown": "$table.markdown",
                },
                reason="отправить отчёт пользователю",
                depends_on=["table"],
                requires_approval=True,
            )
        )
        return Plan(
            goal=goal,
            steps=steps,
            expected_output=f"Таблица из {c.top} лучших товаров и отправка отчёта ({c.channel})",
            source=PlanSource.DETERMINISTIC,
            notes="; ".join(notes) or "план построен детерминированным планировщиком: " + c.describe(),
        )

    # ── Гарантия результата ─────────────────────────────────────────────────
    def _ensure_deliverable(self, plan: Plan) -> list[PlanStep]:
        """План обязан заканчиваться тем, что увидит человек.

        LLM иногда останавливается на промежуточном шаге («получил список»).
        Дописываем таблицу и отправку сами — молча завершать задачу без
        результата платформа не должна.
        """
        steps = list(plan.steps)
        tools = [s.tool for s in steps]
        last_id = steps[-1].id if steps else "search"

        if "build_table" not in tools:
            table_id = f"{last_id}_table"
            steps.append(
                PlanStep(
                    id=table_id,
                    tool="build_table",
                    args={"rows": f"${last_id}.top|items|ranked", "title": "Результат"},
                    reason="представить результат таблицей",
                    depends_on=[last_id],
                )
            )
            last_id = table_id
            tools.append("build_table")

        if "send_report" not in tools:
            table_step = next((s for s in steps if s.tool == "build_table"), None)
            dependency = table_step.id if table_step else last_id
            steps.append(
                PlanStep(
                    id=f"{dependency}_report",
                    tool="send_report",
                    args={
                        "message": "Задача выполнена. Результат в таблице ниже.",
                        "channel": "console",
                        "table_markdown": f"${dependency}.markdown",
                    },
                    reason="доставить результат пользователю",
                    depends_on=[dependency],
                    requires_approval=True,
                )
            )
        return steps
