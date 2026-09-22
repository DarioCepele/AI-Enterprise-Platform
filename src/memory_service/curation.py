"""Reconstruct model context while retaining the full durable transcript.

Prune on reads so summaries and diagnostics can still use the original
messages. In two laboratory turns (40 messages, 12 KB), user messages
accounted for 1.8% of bytes, reasoning 18.9%, assistant 58.0%, and tools
21.4%. Old machinery adds tokens and reduces retrieval precision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CLEARED = "[result removed to make room in the context]"

MEMORY_ID_PREFIX = "memory:"
SUMMARY_ID_PREFIX = f"{MEMORY_ID_PREFIX}summary"


@dataclass(frozen=True)
class ContextPolicy:
    """Rules for reconstructing context."""

    drop_reasoning: bool = True
    keep_tool_results: int = 4
    max_messages: int | None = 60


@dataclass(frozen=True)
class Curation:
    """Report removed content so pruning is observable."""

    kept: int
    reasoning_removed: int
    results_emptied: int
    messages_dropped: int
    summarized: bool = False

    def as_dict(self) -> dict[str, int]:
        return {
            "kept": self.kept,
            "reasoning_removed": self.reasoning_removed,
            "results_emptied": self.results_emptied,
            "messages_dropped": self.messages_dropped,
            "summarized": int(self.summarized),
        }


def _role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else ""


def window_start(messages: list[dict[str, Any]], max_messages: int) -> int:
    """Start the window at a user-turn boundary.

    Compaction uses the same boundary to avoid gaps between summaries and
    retained messages. Choose the boundary before the target cut to retain
    whole tool interactions, even if the window exceeds its target size. Keep
    everything when no boundary exists.
    """
    if len(messages) <= max_messages:
        return 0
    for index in range(len(messages) - max_messages, -1, -1):
        if _role(messages[index]) == "user":
            return index
    return 0


def summary_message(text: str, covers_to_seq: int) -> dict[str, Any]:
    """Create an identifiable system message containing the summary."""
    return {
        "id": f"{SUMMARY_ID_PREFIX}:{covers_to_seq}",
        "role": "system",
        "content": f"Summary of the earlier conversation:\n{text}",
    }


def curate(
    messages: list[dict[str, Any]],
    policy: ContextPolicy,
    summary: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Curation]:
    """Return curated context and removal counts.

    The supplied summary already covers turns leaving the window; model
    inference happens outside this fast read path.
    """
    start = window_start(messages, policy.max_messages) if policy.max_messages else 0
    window = messages[start:]
    dropped = start

    if policy.drop_reasoning:
        kept = [m for m in window if _role(m) != "reasoning"]
        ragionamenti = len(window) - len(kept)
        window = kept
    else:
        ragionamenti = 0

    tool_indexes = [index for index, m in enumerate(window) if _role(m) == "tool"]
    to_clear = set(tool_indexes[: max(len(tool_indexes) - policy.keep_tool_results, 0)])

    curated: list[dict[str, Any]] = []
    for index, message in enumerate(window):
        if index in to_clear:
            curated.append({**message, "content": CLEARED})
        else:
            curated.append(message)

    summarized = bool(summary and dropped)
    if summary is not None and summarized:
        curated = [summary, *curated]

    return curated, Curation(
        kept=len(curated),
        reasoning_removed=ragionamenti,
        results_emptied=len(to_clear),
        messages_dropped=dropped,
        summarized=summarized,
    )
