"""Передача данных между шагами.

Шаг не знает, кто его вызвал: он получает аргументы и возвращает словарь.
Связывает шаги **ссылка** — так план остаётся данными, а не кодом, и его можно
показать человеку до выполнения.

Синтаксис:

- `"$search.items"` — весь объект (список, число, словарь);
- `"$rank.top.0.url"` — поле первого элемента списка;
- `"$rank.top|items|ranked"` — первое существующее поле из перечисленных
  (полезно, когда модель не знает точного имени ключа, но знает смысл);
- `"Нашёл {{search.count}} товаров"` — подстановка внутри текста.

Отдельно проверяется существование шага: ссылка на шаг, которого нет в плане, —
ошибка конфигурации, и она ловится до выполнения, а не в середине задачи.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.formatting import format_money, stringify  # noqa: F401 — stringify реэкспортируется

FULL_REF = re.compile(r"^\$([A-Za-z0-9_-]+)(?:\.(.+))?$")
INLINE_REF = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\.([A-Za-z0-9_.|]+)\s*\}\}")
TRANSFORMS = ("money", "int", "text")


class RefError(RuntimeError):
    """Ссылка не разрешилась: нет шага, нет поля или шаг ещё не выполнялся."""


def get_path(outputs: dict[str, Any], path: str) -> Any:
    """Достать значение по пути `step.field.sub` из результатов шагов."""
    parts = path.split(".", 1)
    step_id = parts[0]
    if step_id not in outputs:
        known = ", ".join(sorted(outputs)) or "нет"
        raise RefError(f"нет результатов шага {step_id!r}; есть: {known}")

    current: Any = outputs[step_id]
    if len(parts) == 1:
        return current

    for segment in parts[1].split("."):
        alternatives = segment.split("|")
        if isinstance(current, dict):
            key = next((a for a in alternatives if a in current), None)
            if key is None:
                raise RefError(f"в шаге {step_id!r} нет поля {segment!r}")
            current = current[key]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError) as exc:
                raise RefError(f"шаг {step_id!r}: нельзя взять {segment!r} из списка") from exc
        else:
            raise RefError(f"шаг {step_id!r}: у значения нет поля {segment!r}")
    return current


def has_reference(value: Any) -> bool:
    """Есть ли в значении ссылка на результаты другого шага.

    Нужно там, где аргумент нельзя проверить до выполнения: план строится
    заранее, а `$search.items` станет списком только после шага search.
    """
    if isinstance(value, str):
        return bool(FULL_REF.match(value.strip()) or INLINE_REF.search(value))
    if isinstance(value, dict):
        return any(has_reference(v) for v in value.values())
    if isinstance(value, list):
        return any(has_reference(v) for v in value)
    return False


TRANSFORMERS: dict[str, Any] = {
    "money": format_money,
    "int": lambda value: str(int(value)),
    "text": stringify,
}


def get_path_with_transform(outputs: dict[str, Any], path: str) -> Any:
    """Путь с необязательным преобразованием: `rank.top.0.price|money`.  """
    if "|" in path:
        head, tail = path.rsplit("|", 1)
        if tail in TRANSFORMERS:
            return TRANSFORMERS[tail](get_path(outputs, head))
    return get_path(outputs, path)


def resolve_value(value: Any, outputs: dict[str, Any]) -> Any:
    """Разрешить ссылки в одном значении (строке, списке или словаре)."""
    if isinstance(value, str):
        full = FULL_REF.match(value.strip())
        if full:
            path = f"{full.group(1)}.{full.group(2)}" if full.group(2) else full.group(1)
            return get_path(outputs, path)

        def substitute(match: re.Match[str]) -> str:
            path = f"{match.group(1)}.{match.group(2)}"
            return stringify(get_path_with_transform(outputs, path))

        return INLINE_REF.sub(substitute, value)
    if isinstance(value, dict):
        return {k: resolve_value(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, outputs) for v in value]
    return value


def resolve_args(args: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    return {k: resolve_value(v, outputs) for k, v in args.items()}


def referenced_steps(args: Any) -> set[str]:
    """Какие шаги упоминаются в аргументах — для проверки зависимостей."""
    found: set[str] = set()
    if isinstance(args, str):
        full = FULL_REF.match(args.strip())
        if full:
            found.add(full.group(1))
        found.update(m.group(1) for m in INLINE_REF.finditer(args))
    elif isinstance(args, dict):
        for value in args.values():
            found |= referenced_steps(value)
    elif isinstance(args, list):
        for value in args:
            found |= referenced_steps(value)
    return found


def alias_map(steps: list[Any]) -> dict[str, str]:
    """Псевдонимы шагов: инструмент `search_products` отвечает и на `$search`.

    Модель не знает, как мы назовём шаги, и в ссылках пишет смысл, а не
    идентификатор (`$search.items` вместо `$step1.items`). Вместо того чтобы
    требовать угадать имя, ссылки приводятся к реальным id шагов.
    """
    aliases: dict[str, str] = {}
    for step in steps:
        names = {step.id, step.tool}
        parts = str(step.tool).split("_")
        for index in range(1, len(parts)):
            names.add("_".join(parts[:index]))  # build_table → build, search_products → search
            names.add("_".join(parts[index:]))  # build_table → table, search_products → products
        for name in names:
            aliases.setdefault(name, step.id)
    return aliases


def _rewrite_text(value: str, aliases: dict[str, str]) -> str:
    full = FULL_REF.match(value.strip())
    if full:
        target = aliases.get(full.group(1), full.group(1))
        return "$" + target + (f".{full.group(2)}" if full.group(2) else "")
    return INLINE_REF.sub(
        lambda m: "{{" + aliases.get(m.group(1), m.group(1)) + "." + m.group(2) + "}}", value
    )


def _rewrite(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _rewrite_text(value, aliases)
    if isinstance(value, dict):
        return {k: _rewrite(v, aliases) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, aliases) for v in value]
    return value


def normalize_references(steps: list[Any]) -> list[Any]:
    """Заменить смысловые имена шагов в ссылках на их реальные идентификаторы."""
    aliases = alias_map(steps)
    return [
        step.model_copy(
            update={
                "args": _rewrite(step.args, aliases),
                "depends_on": [aliases.get(d, d) for d in step.depends_on],
            }
        )
        for step in steps
    ]


def ordered_steps(steps: list[Any]) -> list[Any]:
    """Топологический порядок по `depends_on` + ссылкам в аргументах.

    Цикл или ссылка вперёд — ошибка плана: такой план нельзя выполнить
    последовательно, и лучше узнать об этом сразу.
    """
    by_id = {s.id: s for s in steps}
    deps: dict[str, set[str]] = {}
    for step in steps:
        needed = set(step.depends_on) | referenced_steps(step.args)
        needed.discard(step.id)
        unknown = needed - set(by_id)
        if unknown:
            raise RefError(
                f"шаг {step.id} ссылается на шаги, которых нет в плане: {sorted(unknown)}"
            )
        deps[step.id] = needed

    ordered: list[Any] = []
    done: set[str] = set()
    pending = list(by_id)
    while pending:
        ready = [sid for sid in pending if deps[sid] <= done]
        if not ready:
            cycle = ", ".join(sorted(pending))
            raise RefError(f"план содержит цикл или ссылку вперёд: {cycle}")
        for sid in ready:
            ordered.append(by_id[sid])
            done.add(sid)
            pending.remove(sid)
    return ordered
