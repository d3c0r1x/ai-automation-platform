"""Мини-помощник проверок, работающий и с pytest, и без него.

Нужен, чтобы тесты не зависели от pytest-фикстур: в CI pytest есть, а в
ограниченном окружении (только рантайм-зависимости) — нет, и набор должен
запускаться там же, где запускается демо.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Type


class AssertionFailed(AssertionError):
    pass


@contextmanager
def raises(exc_type: Type[BaseException], contains: str = "") -> Iterator[Any]:
    try:
        yield
    except exc_type as error:  # ожидаемое исключение
        if contains and contains not in str(error):
            raise AssertionFailed(
                f"{exc_type.__name__} выброшено, но текст «{contains}» не найден: {error}"
            ) from None
    else:
        raise AssertionFailed(f"ожидалось {exc_type.__name__}, но исключения не было")


def approx(value: float, tolerance: float = 1e-6) -> Any:
    class _Approx:
        def __eq__(self, other: Any) -> bool:
            return abs(float(other) - value) <= tolerance

        def __repr__(self) -> str:  # pragma: no cover
            return f"≈{value}"

    return _Approx()


def truthy(value: Any, message: str = "") -> None:
    if not value:
        raise AssertionFailed(message or f"ожидалось истинное значение, получено {value!r}")
