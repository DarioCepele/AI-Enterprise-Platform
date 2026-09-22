"""Durable facts remain useful across conversations.

Summaries describe one thread; facts belong to the scope, so deleting a
conversation does not erase knowledge about the user.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .curation import MEMORY_ID_PREFIX

logger = logging.getLogger(__name__)

_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class Fact:
    """A fact with a stable key and a mutable value."""

    key: str
    value: str


def parse_facts(raw: str) -> list[Fact]:
    """Parse model-generated facts, allowing Markdown fences but rejecting
    missing keys or values.

    Invalid JSON returns no facts rather than failing the conversation.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Unreadable facts, ignored: %s", text[:120])
        return []

    if isinstance(payload, dict):
        payload = payload.get("facts") or []
    if not isinstance(payload, list):
        return []

    facts: list[Fact] = []
    seen: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip().lower()
        value = str(entry.get("value") or "").strip()
        if not _KEY.match(key) or not value:
            continue
        if key in seen:
            logger.info(
                "Key '%s' repeated in the extraction: keeping the first value.", key
            )
            continue
        seen.add(key)
        facts.append(Fact(key=key, value=value))
    return facts


def facts_message(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Create an identifiable system message containing known facts."""
    lines = "\n".join(f"- {fact['key']}: {fact['value']}" for fact in facts)
    return {
        "id": f"{MEMORY_ID_PREFIX}facts",
        "role": "system",
        "content": (
            "Things you already know about this user, from earlier conversations:\n"
            f"{lines}\n"
            "Use them if useful. If the user contradicts them, what they say now wins."
        ),
    }
