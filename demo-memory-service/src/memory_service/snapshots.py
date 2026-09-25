"""Compute new turns from full thread snapshots.

The service owns delta detection because it can compare incoming messages with
durable history.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .curation import MEMORY_ID_PREFIX
from .models import NewMessage, StoredMessage


def _role_of(raw: dict[str, Any]) -> str:
    role = raw.get("role")
    return role if isinstance(role, str) and role else "unknown"


def _content_of(raw: dict[str, Any]) -> str:
    content = raw.get("content")
    return content if isinstance(content, str) else ""


def new_messages(
    stored: Sequence[StoredMessage],
    incoming: Sequence[dict[str, Any]],
) -> list[NewMessage]:
    """Find messages not yet stored.

    Prefer external identifiers, which survive reordering and rewritten
    snapshots. For anonymous messages, use position and conservatively count
    every stored message to avoid duplicates.
    """
    known_ids = {message.external_id for message in stored if message.external_id}
    fresh: list[NewMessage] = []

    for index, raw in enumerate(incoming):
        external_id = raw.get("id")
        if isinstance(external_id, str) and external_id.startswith(MEMORY_ID_PREFIX):
            continue
        if isinstance(external_id, str) and external_id:
            if external_id in known_ids:
                continue
        elif index < len(stored):
            continue

        fresh.append(
            NewMessage(
                role=_role_of(raw),
                content=_content_of(raw),
                external_id=external_id if isinstance(external_id, str) else None,
                payload=raw,
            )
        )

    return fresh
