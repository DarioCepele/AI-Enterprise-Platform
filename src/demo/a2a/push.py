"""Subagent push notifications: who receives them, and how they are trusted."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

HEADER = "X-A2A-Notification-Token"


def _secret() -> bytes:
    return os.getenv("DEMO_PUSH_SECRET", "laboratory-without-a-secret").encode()


def token_for(thread_id: str) -> str:
    """The token the subagent will send back with the notification.

    It signs the **thread**, not the task: the webhook is registered before the
    task exists, so a token over the task id could not be computed in advance.

    Signed and not stored: verifying it needs no process state, so two replicas
    accept the same tokens without remembering anything. The secret lives in the
    environment.
    """
    return hmac.new(_secret(), thread_id.encode(), hashlib.sha256).hexdigest()


def token_is_valid(thread_id: str, received: str | None) -> bool:
    if not received:
        return False
    return hmac.compare_digest(token_for(thread_id), received)


def webhook_url(base: str, scope: str, thread_id: str) -> str:
    """The correlation lives in the URL: the receiver already knows the thread.

    The alternative -- a task-to-thread table -- would be process state, or one
    more query on every notification.
    """
    return f"{base.rstrip('/')}/a2a/push/{scope}/{thread_id}"


TERMINAL = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


def summary_of(notification: dict[str, Any]) -> tuple[str, str, str]:
    """Extracts (task_id, state, text) from a notification, without trusting its shape.

    The subagent notifies **one event at a time**, not only the end, and sends
    them as StreamResponse in camelCase: sometimes a whole task, sometimes a
    status update, sometimes an artifact. Everything is accepted here and the
    caller decides what to ignore.
    """
    task = notification.get("task") or {}
    status_update = notification.get("statusUpdate") or notification.get("status_update") or {}
    artifact_update = notification.get("artifactUpdate") or notification.get("artifact_update") or {}

    task_id = str(
        task.get("id")
        or status_update.get("taskId")
        or status_update.get("task_id")
        or artifact_update.get("taskId")
        or artifact_update.get("task_id")
        or ""
    )
    state = str(
        (task.get("status") or {}).get("state")
        or (status_update.get("status") or {}).get("state")
        or ""
    )

    artifacts = list(task.get("artifacts") or [])
    if artifact_update.get("artifact"):
        artifacts.append(artifact_update["artifact"])
    parts = [
        part["text"]
        for artifact in artifacts
        for part in artifact.get("parts") or []
        if isinstance(part.get("text"), str)
    ]
    return task_id, state, "".join(parts).strip()


def is_terminal(state: str) -> bool:
    """Whether this notification closes the task or is only progress."""
    return state in TERMINAL
