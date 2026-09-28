"""Subagent push notifications: who receives them, and how they are trusted.

The token signs exactly the correlation the webhook URL carries -- scope,
thread, agent -- and the time window it was issued in: a captured token stops
working within two windows, cannot be replayed into another scope or thread,
and cannot pretend to come from another agent. Signed and not stored: any
replica verifies what any other issued. The primitives are the platform's
(`platform_core.push`); nothing here has a default secret.
"""

from __future__ import annotations

import logging
from typing import Any

from platform_core import push

from ..config import get_settings

logger = logging.getLogger(__name__)

HEADER = push.HEADER
TERMINAL = push.TERMINAL
summary_of = push.summary_of


def token_for(scope: str, thread_id: str, agent: str, at: float | None = None) -> str:
    """The token the subagent will send back with the notification.

    It signs the **thread**, not the task: the webhook is registered before the
    task exists, so a token over the task id could not be computed in advance.
    """
    settings = get_settings()
    return push.sign_windowed(
        settings.push_secret,
        scope,
        thread_id,
        agent,
        window_seconds=settings.push_window_seconds,
        at=at,
    )


def token_is_valid(
    scope: str,
    thread_id: str,
    agent: str,
    received: str | None,
    at: float | None = None,
) -> bool:
    """Accepts the current window and the one before it."""
    settings = get_settings()
    return push.verify_windowed(
        settings.push_secret,
        received,
        scope,
        thread_id,
        agent,
        window_seconds=settings.push_window_seconds,
        at=at,
    )


def webhook_url(base: str, scope: str, thread_id: str, agent: str) -> str:
    """The correlation lives in the URL: the receiver already knows the thread.

    The alternative -- a task-to-thread table -- would be process state, or one
    more query on every notification.
    """
    return f"{base.rstrip('/')}/a2a/push/{scope}/{thread_id}/{agent}"


def is_terminal(state: str) -> bool:
    """Whether this notification closes the task or is only progress."""
    return state in TERMINAL


class SeenNotifications:
    """Remembers which notifications already landed, so one is written once.

    A2A delivery is at-least-once: the same outcome can arrive twice, and twice
    in the conversation is a duplicate the user reads. Shared through the
    database when it is configured, because two replicas that each remember
    their own would still write it twice. Old rows are swept as they are
    written: a notification older than a day cannot be a duplicate of anything
    still in flight.
    """

    def __init__(self, pool: Any | None = None, ttl_seconds: int = 86_400) -> None:
        self._pool = pool
        self._ttl = ttl_seconds
        self._local: set[str] = set()

    @staticmethod
    def _key(thread_id: str, task_id: str, state: str) -> str:
        return f"push:{thread_id}:{task_id}:{state}"

    async def first_time(self, thread_id: str, task_id: str, state: str) -> bool:
        key = self._key(thread_id, task_id, state)
        if self._pool is None:
            if key in self._local:
                return False
            self._local.add(key)
            return True
        try:
            await self._pool.open()
            async with self._pool.connection() as connection:
                await connection.execute(
                    "DELETE FROM seen_notifications "
                    "WHERE seen_at < now() - %s * interval '1 second'",
                    (self._ttl,),
                )
                written = await connection.execute(
                    "INSERT INTO seen_notifications (key) VALUES (%s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (key,),
                )
            return (written.rowcount or 0) == 1
        except Exception:
            logger.warning(
                "Notification memory unreachable: accepting %s without deduplication.",
                key,
                exc_info=True,
            )
            return True
