"""Инструмент сохранения артефактов (CSV, markdown, JSON).

Артефакт пишется в каталог `data/artifacts/`, имя файла обеззараживается:
агент (а значит, и внешний текст, который он прочитал) не должен иметь
возможности записать файл через `../../`. Это тот же класс ошибок, что и
path traversal в веб-приложении, поэтому защита здесь явная.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import Field

from app.core.tools.base import Tool, ToolContext, ToolError, ToolParams

SAFE_NAME = re.compile(r"[^A-Za-z0-9А-Яа-яЁё._-]+")
ALLOWED_SUFFIXES = {".csv", ".md", ".json", ".txt"}


class SaveArtifactParams(ToolParams):
    name: str = Field(description="Имя файла, например items.csv")
    content: str = Field(description="Содержимое файла")
    suffix: str = Field(default="", description="Расширение, если его нет в имени")


def safe_filename(name: str, suffix: str = "") -> str:
    cleaned = SAFE_NAME.sub("_", (name or "").strip()).strip("._") or "artifact"
    cleaned = cleaned[:120]
    if not Path(cleaned).suffix:
        cleaned += suffix if suffix.startswith(".") else (suffix or ".txt")
    if Path(cleaned).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ToolError(f"недопустимое расширение файла: {Path(cleaned).suffix}")
    return cleaned


async def save_artifact(ctx: ToolContext, params: SaveArtifactParams) -> dict[str, Any]:
    filename = safe_filename(params.name, params.suffix)
    directory = Path(ctx.artifacts_dir or "data/artifacts")
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / filename).resolve()
    if directory.resolve() not in path.parents:
        raise ToolError("путь выходит за пределы каталога артефактов")

    path.write_text(params.content, encoding="utf-8")
    return {"path": str(path), "name": filename, "bytes": len(params.content.encode("utf-8"))}


SAVE_ARTIFACT = Tool(
    name="save_artifact",
    description="Сохранить файл с результатом (CSV, markdown, JSON) на диск.",
    params=SaveArtifactParams,
    run=save_artifact,
    tags=("storage",),
)
