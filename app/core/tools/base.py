"""Базовые типы инструментов и реестр.

Инструмент — это функция с **типизированными** аргументами (pydantic-модель).
Из неё автоматически получается JSON-схема для LLM tool calling: одна и та же
модель описывает функцию и для агента, и для валидации того, что агент прислал.
Поэтому модель не может вызвать инструмент с несуществующим полем — это
отбракуется до исполнения, а не в середине шага.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.core.agent.refs import has_reference


class ToolError(RuntimeError):
    """Ошибка инструмента, которую можно показать пользователю."""


@dataclass
class ToolContext:
    """Всё, что нужно инструменту из внешнего мира.

    Передаётся явно, а не берётся из глобального состояния: так демо-режим,
    тесты и прод-запуск используют один и тот же код инструментов.
    """

    artifacts_dir: Any = None  # pathlib.Path
    http_timeout: int = 30
    demo_mode: bool = True
    settings: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolParams(BaseModel):
    """Базовый класс аргументов инструмента."""


Runner = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[ToolParams]
    run: Runner
    requires_approval: bool = False
    tags: tuple[str, ...] = ()

    def json_schema(self) -> dict[str, Any]:
        """Схема в формате OpenAI tool calling."""
        schema = self.params.model_json_schema()
        # pydantic добавляет служебные ключи, которые LLM не нужны.
        for unwanted in ("title", "$defs", "definitions"):
            schema.pop(unwanted, None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class UnknownToolError(ToolError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        """Зарегистрировать инструмент. Возвращает реестр — удобно для цепочки."""
        if tool.name in self._tools:
            raise ValueError(f"инструмент {tool.name!r} уже зарегистрирован")
        self._tools[tool.name] = tool
        return self

    def replace(self, tool: Tool) -> Tool:
        """Заменить инструмент (например, на другой транспорт в этом окружении)."""
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(sorted(self._tools)) or "нет"
            raise UnknownToolError(f"неизвестный инструмент {name!r}; доступны: {known}") from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        names = sorted(only) if only else self.names()
        return [self.get(n).json_schema() for n in names]

    def describe(self) -> list[dict[str, Any]]:
        """Короткое описание для UI и `/api/tools`."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "requires_approval": t.requires_approval,
                "tags": list(t.tags),
                "params": sorted(t.params.model_fields),
            }
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def validate_args(self, name: str, args: dict[str, Any]) -> ToolParams:
        """Строгая проверка перед выполнением: ссылки уже разрешены в значения."""
        tool = self.get(name)
        try:
            return tool.params.model_validate(args)
        except Exception as exc:  # noqa: BLE001 — текст нужен агенту как есть
            raise ToolError(f"аргументы {name} не прошли валидацию: {exc}") from exc

    def check_args(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Проверка аргументов **плана**: ссылки на другие шаги разрешены.

        План строится до выполнения, поэтому значения вида `$search.items`
        проверить нельзя — они станут списком позже. Что можно и нужно
        проверить сразу: имена полей, обязательные поля и типы всех
        «обычных» значений. Так опечатка модели ловится до запуска,
        а не после первого сетевого запроса.
        """
        tool = self.get(name)
        fields = tool.params.model_fields
        unknown = [k for k in args if k not in fields]
        if unknown:
            raise ToolError(f"{name}: неизвестные поля {unknown}; допустимы: {sorted(fields)}")
        missing = [k for k, f in fields.items() if f.is_required() and k not in args]
        if missing:
            raise ToolError(f"{name}: не переданы обязательные поля {missing}")
        for key, value in args.items():
            if has_reference(value):
                continue
            try:
                TypeAdapter(fields[key].annotation).validate_python(value)
            except ValidationError as exc:
                first = exc.errors()[0]
                raise ToolError(f"{name}.{key}: {first.get('msg', 'неверное значение')}") from exc
        return dict(args)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def build_registry() -> ToolRegistry:  # pragma: no cover - сборка, проверяется тестами
    """Реестр инструментов по умолчанию (импорты локальные, чтобы не тянуть
    Playwright и httpx туда, где они не нужны)."""
    from app.core.tools.browser import OPEN_PAGE
    from app.core.tools.marketplace import SEARCH_PRODUCTS
    from app.core.tools.notify import SEND_REPORT
    from app.core.tools.ranking import RANK_PRODUCTS
    from app.core.tools.storage import SAVE_ARTIFACT
    from app.core.tools.tables import BUILD_TABLE

    registry = ToolRegistry()
    for tool in (SEARCH_PRODUCTS, RANK_PRODUCTS, OPEN_PAGE, BUILD_TABLE, SEND_REPORT, SAVE_ARTIFACT):
        registry.register(tool)
    return registry
