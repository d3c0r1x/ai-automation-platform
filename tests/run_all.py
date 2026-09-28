"""Запуск набора тестов без pytest.

    python tests/run_all.py            # все тесты
    python tests/run_all.py refs tools # только модули, где встречается подстрока

Зачем: репозиторий должен проверяться там же, где запускается демо — например,
в ограниченном окружении, где из зависимостей стоят только рантайм-пакеты.
В CI основной прогон всё равно через `pytest -q`: там есть и отчётность, и
подсчёт покрытия.

Тесты, которым нужны необязательные зависимости (FastAPI), помечаются
пропущенными через `unittest.SkipTest` — так же, как это делает pytest.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
import time
import traceback
import unittest
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TESTS_DIR = Path(__file__).resolve().parent


def discover(filters: list[str]) -> list[str]:
    modules = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        name = f"tests.{path.stem}"
        if filters and not any(f in path.stem for f in filters):
            continue
        modules.append(name)
    return modules


def collect(module: Any) -> list[Callable[[], Any]]:
    tests: list[Callable[[], Any]] = []
    for name, obj in vars(module).items():
        if name.startswith("test_") and callable(obj):
            tests.append(obj)
    return sorted(tests, key=lambda fn: fn.__name__)


def run_test(fn: Callable[[], Any]) -> None:
    if inspect.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()


def main(argv: list[str]) -> int:
    filters = [a for a in argv if not a.startswith("-")]
    modules = discover(filters)
    passed = failed = skipped = 0
    failures: list[tuple[str, str]] = []
    started = time.perf_counter()

    for module_name in modules:
        try:
            module = importlib.import_module(module_name)
        except unittest.SkipTest as exc:
            print(f"skip  {module_name} ({exc})")
            skipped += 1
            continue
        except Exception:
            print(f"FAIL  {module_name} (не импортируется)")
            traceback.print_exc()
            failed += 1
            continue

        for test in collect(module):
            label = f"{module_name}.{test.__name__}"
            try:
                run_test(test)
            except unittest.SkipTest as exc:
                skipped += 1
                print(f"skip  {label} — {exc}")
            except AssertionError as exc:
                failed += 1
                failures.append((label, str(exc) or "утверждение не выполнено"))
                print(f"FAIL  {label}: {exc}")
            except Exception as exc:
                failed += 1
                failures.append((label, f"{type(exc).__name__}: {exc}"))
                print(f"FAIL  {label}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"ok    {label}")

    elapsed = time.perf_counter() - started
    print("-" * 60)
    print(f"итог: {passed} passed, {failed} failed, {skipped} skipped за {elapsed:.2f} с")
    if failures:
        print("провалы:")
        for label, reason in failures:
            print(f"  - {label}: {reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
