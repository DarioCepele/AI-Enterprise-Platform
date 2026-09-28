"""The A2A server side of push notifications: what is sent, and where to.

Two rules, both enforced here so that every agent of the platform follows them
without re-implementing them:

- **Where**: a notification goes only to a URL the operator allowed
  (`PushURLPolicy`). The check happens when the webhook is registered -- the
  caller gets an error, not silence -- and again when it is dispatched, so a
  store filled by some other path cannot be used to reach a forbidden URL.
- **What**: the SDK notifies every event on the queue; while streaming that is
  dozens of POSTs per task, all discarded by the receiver. Only the moments
  where the caller has something to do are sent: the end of the task, and a
  request for a clarification.

Requires `a2a-sdk` (the `a2a` extra).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from a2a.server.context import ServerCallContext
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationEvent,
    TaskStore,
)
from a2a.types import TaskPushNotificationConfig, TaskState
from a2a.utils.errors import InvalidParamsError

from .push import PushURLPolicy

logger = logging.getLogger(__name__)

WORTH_NOTIFYING = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
    }
)


class AllowlistedPushConfigStore(PushNotificationConfigStore):
    """A push configuration store that only ever holds allowed URLs."""

    def __init__(
        self, inner: PushNotificationConfigStore, policy: PushURLPolicy
    ) -> None:
        self._inner = inner
        self._policy = policy

    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        if not self._policy.allows(notification_config.url):
            logger.warning(
                "Push webhook refused for task %s: the URL is not in the allowlist.",
                task_id[:8],
            )
            raise InvalidParamsError(
                "push notification URL not allowed by this agent's policy"
            )
        await self._inner.set_info(task_id, notification_config, context)

    async def get_info(
        self, task_id: str, context: ServerCallContext
    ) -> list[TaskPushNotificationConfig]:
        return await self._inner.get_info(task_id, context)

    async def get_info_for_dispatch(
        self, task_id: str
    ) -> list[TaskPushNotificationConfig]:
        configs = await self._inner.get_info_for_dispatch(task_id)
        allowed = [config for config in configs if self._policy.allows(config.url)]
        if len(allowed) != len(configs):
            logger.warning(
                "Push for task %s: %d webhook(s) outside the allowlist dropped.",
                task_id[:8],
                len(configs) - len(allowed),
            )
        return allowed

    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        await self._inner.delete_info(task_id, context, config_id)


def _state_of(event: PushNotificationEvent) -> int | None:
    status = getattr(event, "status", None)
    if status is None:
        return None
    state: int = status.state
    return state


class EssentialPushSender(BasePushNotificationSender):
    """Notifies only the points where the caller has something to do."""

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        state = _state_of(event)
        if state not in WORTH_NOTIFYING:
            return
        logger.info("Push notification for task %s: state %s.", task_id[:8], state)
        await super().send_notification(task_id, event)


Closer = Callable[[], Awaitable[None]]

_DURABLE: dict[
    tuple[str, str, str], tuple[TaskStore, PushNotificationConfigStore, Any]
] = {}


async def _nothing_to_close() -> None:
    return None


def sqlalchemy_url(dsn: str) -> str:
    """A libpq-style DSN in the form SQLAlchemy's async engine expects."""
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+asyncpg://" + dsn[len(prefix) :]
    return dsn


def task_stores(
    dsn: str, *, name: str, encryption_key: str = ""
) -> tuple[TaskStore, PushNotificationConfigStore, Closer]:
    """Where tasks and their webhooks live: Postgres when a DSN is given, else RAM.

    A task somebody is waiting on -- a durable process step, a clarification
    that comes back tomorrow -- has to survive a restart of the agent working
    on it: in memory it would not. Each agent gets its own tables (`name`), so
    several agents can share one database without reading each other's tasks.
    Webhook tokens are encrypted at rest when a Fernet key is configured.
    """
    if not dsn:
        logger.warning(
            "Tasks and webhooks kept in memory: they are lost when %s restarts.", name
        )
        return (
            InMemoryTaskStore(),
            InMemoryPushNotificationConfigStore(),
            _nothing_to_close,
        )

    key = (sqlalchemy_url(dsn), name, encryption_key)
    cached = _DURABLE.get(key)
    if cached is None:
        from a2a.server.tasks.database_push_notification_config_store import (
            DatabasePushNotificationConfigStore,
        )
        from a2a.server.tasks.database_task_store import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import create_async_engine

        # One engine and one pair of stores per process: SQLAlchemy registers
        # each table once, and an app built twice (tests do) must not redefine it.
        engine = create_async_engine(key[0], pool_pre_ping=True)
        cached = (
            DatabaseTaskStore(engine, table_name=f"{name}_tasks"),
            DatabasePushNotificationConfigStore(
                engine,
                table_name=f"{name}_push_configs",
                encryption_key=encryption_key or None,
            ),
            engine,
        )
        _DURABLE[key] = cached
    tasks, webhooks, engine = cached
    logger.info("Tasks and webhooks of %s kept in Postgres.", name)

    async def close() -> None:
        # Disposing closes the pooled connections; the engine stays usable.
        await engine.dispose()

    return tasks, webhooks, close


def push_components(
    inner: PushNotificationConfigStore,
    policy: PushURLPolicy,
    *,
    timeout: float = 10.0,
) -> tuple[AllowlistedPushConfigStore, EssentialPushSender]:
    """The store and the sender an A2A request handler needs, wired together."""
    if not policy.prefixes:
        logger.warning(
            "No push notification receivers allowed: webhooks will be refused. "
            "Name them to turn push notifications on."
        )
    store = AllowlistedPushConfigStore(inner, policy)
    sender = EssentialPushSender(httpx.AsyncClient(timeout=timeout), store)
    return store, sender
