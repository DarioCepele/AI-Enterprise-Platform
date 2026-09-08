"""Summarize old turns through a dedicated model interface. Compaction requires inference, runs outside the response path, and reports missing configuration explicitly."""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

INSTRUCTIONS = """Summarize the conversation below for an agent that has to continue it.

Keep:
- the decisions taken and the conclusions reached;
- the facts, numbers and names that were established;
- whatever was pending or unresolved;
- what the user asked for, with their still-open requests.

Drop:
- exact phrasings and pleasantries;
- intermediate steps and the results of tools already used;
- anything the model can work out on its own.

Write in Italian, in dry prose, ten lines at most. Invent nothing that is not
in the conversation: if a point is unclear, say so."""


FACTS_INSTRUCTIONS = """Extract from the conversation the durable facts about the user and their work.

A durable fact stays true in a different conversation, tomorrow: identities and
roles, stable preferences, constraints, project names, decisions taken. The
state of this conversation is not one, nor is what the user is asking right
now, nor information the model brought in from outside.

Answer **only** with a JSON array of {"key", "value"} objects, where the key is
a short lowercase label with underscores (for instance "contact" or
"preferred_language") and the value is concise. The key exists to recognize the
same fact when its value changes: use the same label for the same thing.

If there is no durable fact, answer with an empty array. Do not invent."""


class Summarizer(Protocol):
    """Convert messages into a prose summary."""

    async def summarize(self, messages: list[dict[str, Any]]) -> str: ...


class FactExtractor(Protocol):
    """Extract durable facts from messages as JSON."""

    async def extract_facts(self, messages: list[dict[str, Any]]) -> str: ...


class NoSummarizer:
    """Report missing model configuration and produce no summary."""

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        logger.warning(
            "Summary not produced: no model configured (MEMORY_SUMMARY_MODEL). "
            "The turns outside the window stay outside the context."
        )
        return ""


class OpenAICompatibleSummarizer:
    """Summarize through a Chat Completions endpoint with a dedicated model and credentials. Compatible providers include OpenRouter and LM Studio."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        return await self._ask(INSTRUCTIONS, messages)

    async def extract_facts(self, messages: list[dict[str, Any]]) -> str:
        """Extract facts separately from summaries so malformed JSON does not discard a valid summary."""
        return await self._ask(FACTS_INSTRUCTIONS, messages)

    async def _ask(self, instructions: str, messages: list[dict[str, Any]]) -> str:
        transcript = "\n".join(_readable(message) for message in messages)
        response = await self._client.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": transcript},
                ],
                "temperature": 0,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return (payload["choices"][0]["message"]["content"] or "").strip()

    async def aclose(self) -> None:
        await self._client.aclose()


def _readable(message: dict[str, Any]) -> str:
    """Render a message for the summarizer. Tool calls become readable notes describing usage instead of wire-format JSON."""
    role = message.get("role", "?")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        calls = message.get("toolCalls") or message.get("tool_calls")
        if calls:
            names = ", ".join(
                str((call.get("function") or {}).get("name") or call.get("name") or "?")
                for call in calls
                if isinstance(call, dict)
            )
            return f"[{role}] called: {names}"
        content = json.dumps(content, ensure_ascii=False) if content else ""
    return f"[{role}] {content}"
