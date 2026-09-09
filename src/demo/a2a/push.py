"""Subagent push notifications: who receives them, and how they are trusted."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

HEADER = "X-A2A-Notification-Token"

DEFAULT_WINDOW_SECONDS = 3600


def _secret() -> bytes:
    return os.getenv("DEMO_PUSH_SECRET", "laboratory-without-a-secret").encode()


def _window_seconds() -> int:
    return int(os.getenv("DEMO_PUSH_WINDOW_SECONDS", str(DEFAULT_WINDOW_SECONDS)))


def token_for(thread_id: str, at: float | None = None) -> str:
    """The token the subagent will send back with the notification.

    It signs the **thread**, not the task: the webhook is registered before the
    task exists, so a token over the task id could not be computed in advance.

    It also signs the **time window**, because a token that never expires is a
    key that whoever intercepts it keeps: this way a captured one stops working
    within two windows.

    Signed and not stored: verifying it needs no process state, so two replicas
    accept the same tokens without remembering anything. The secret lives in the
    environment.
    """
    window = int((at if at is not None else time.time()) // _window_seconds())
    payload = f"{thread_id}:{window}".encode()
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def token_is_valid(thread_id: str, received: str | None, at: float | None = None) -> bool:
    """Accepts the current window and the one before it.

    The subagent registers the webhook when the task starts and calls back when
    it ends: without a window of slack, a task slower than the clock's rounding
    would deliver a token that was valid when it was made and is not any more.
    """
    if not received:
        return False
    now = at if at is not None else time.time()
    return any(
        hmac.compare_digest(token_for(thread_id, at=now - offset * _window_seconds()), received)
        for offset in (0, 1)
    )


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


class SeenNotifications:
    """Remembers which notifications already landed, so one is written once.

    A2A delivery is at-least-once: the same outcome can arrive twice, and twice
    in the conversation is a duplicate the user reads. Shared through Redis when
    it is configured, because two replicas that each remember their own would
    still write it twice.
    """

    def __init__(self, redis: Any | None = None, ttl_seconds: int = 86_400) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._local: set[str] = set()

    @staticmethod
    def _key(thread_id: str, task_id: str, state: str) -> str:
        return f"push:{thread_id}:{task_id}:{state}"

    async def first_time(self, thread_id: str, task_id: str, state: str) -> bool:
        key = self._key(thread_id, task_id, state)
        if self._redis is None:
            if key in self._local:
                return False
            self._local.add(key)
            return True
        try:
            return bool(await self._redis.set(key, "1", nx=True, ex=self._ttl))
        except Exception:
            logger.warning(
                "Notification memory unreachable: accepting %s without deduplication.",
                key,
                exc_info=True,
            )
            return True
