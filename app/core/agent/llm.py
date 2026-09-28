"""Клиент LLM с tool calling.

Поддерживается любой OpenAI-совместимый эндпоинт: OpenAI, OpenRouter, локальная
Ollama (`http://localhost:11434/v1`). Ключ и адрес — только из окружения.

Поведение при отказе модели намеренно простое: клиент **не** подменяет ответ
своими догадками, а бросает `LlmUnavailable`. Решение «работать без модели»
принимает планировщик и явно помечает это в плане (`source=deterministic`),
чтобы в дашборде и в отчёте было видно, кто строил план.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.agent.prompts import SYSTEM_PLANNER, SYSTEM_SUMMARY, planner_user_prompt, summary_user_prompt


class LlmUnavailable(RuntimeError):
    """Модель недоступна или ответила некорректно — вызывающий код решает, что делать."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


class LlmClient(Protocol):
    @property
    def available(self) -> bool: ...

    async def plan(self, goal: str, tools: list[dict[str, Any]], max_steps: int) -> tuple[list[ToolCall], str]: ...

    async def summarize(self, goal: str, facts: dict[str, Any]) -> str: ...


class NullLlmClient:
    """Заглушка для демо-режима: модель не настроена, планировщик падает на код."""

    @property
    def available(self) -> bool:
        return False

    async def plan(self, goal: str, tools: list[dict[str, Any]], max_steps: int) -> tuple[list[ToolCall], str]:
        raise LlmUnavailable("LLM не настроена (нет AAP_LLM_BASE_URL/AAP_LLM_API_KEY)")

    async def summarize(self, goal: str, facts: dict[str, Any]) -> str:
        raise LlmUnavailable("LLM не настроена")


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._base and self._key)

    async def _chat(self, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        payload = {"model": self._model, "messages": messages, "temperature": 0, **extra}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmUnavailable(f"сеть недоступна: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise LlmUnavailable(f"провайдер ответил {response.status_code}: {response.text[:200]}")
        try:
            return response.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LlmUnavailable(f"неожиданный ответ провайдера: {response.text[:200]}") from exc

    async def plan(self, goal: str, tools: list[dict[str, Any]], max_steps: int) -> tuple[list[ToolCall], str]:
        names = [t["function"]["name"] for t in tools]
        message = await self._chat(
            [
                {"role": "system", "content": SYSTEM_PLANNER},
                {"role": "user", "content": planner_user_prompt(goal, names, max_steps)},
            ],
            tools=tools,
            tool_choice="auto",
        )
        raw_calls = message.get("tool_calls") or []
        calls: list[ToolCall] = []
        for call in raw_calls[:max_steps]:
            function = call.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                # Модель прислала не JSON: шаг бесполезен, но и падать не из-за чего —
                # валидация плана ниже отбросит его и запишет причину в notes.
                arguments = {"__invalid_json__": raw_args}
            calls.append(ToolCall(name=name, arguments=arguments))
        if not calls:
            raise LlmUnavailable("модель не предложила ни одного вызова инструмента")
        return calls, str(message.get("content") or "")

    async def summarize(self, goal: str, facts: dict[str, Any]) -> str:
        message = await self._chat(
            [
                {"role": "system", "content": SYSTEM_SUMMARY},
                {"role": "user", "content": summary_user_prompt(goal, facts)},
            ]
        )
        text = str(message.get("content") or "").strip()
        if not text:
            raise LlmUnavailable("модель вернула пустую сводку")
        return text


def build_llm_client(settings: Any) -> LlmClient:
    if settings.llm_base_url and settings.llm_api_key:
        return OpenAICompatClient(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout
        )
    return NullLlmClient()
