"""A2A server side: webhooks only where allowed, extended card only with a token."""

from __future__ import annotations

import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import AgentCard, TaskPushNotificationConfig
from a2a.utils.errors import ExtendedAgentCardNotConfiguredError, InvalidParamsError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from platform_core.a2a_server import AllowlistedPushConfigStore
from platform_core.push import PushURLPolicy
from platform_core.service_token import ServiceToken

POLICY = PushURLPolicy(("http://master-agent:8000/a2a/push/",))


def config(url: str) -> TaskPushNotificationConfig:
    return TaskPushNotificationConfig(task_id="task-1", url=url, token="tok")


async def test_an_allowed_webhook_is_stored_and_dispatched():
    store = AllowlistedPushConfigStore(InMemoryPushNotificationConfigStore(), POLICY)
    await store.set_info(
        "task-1", config("http://master-agent:8000/a2a/push/s/t/a"), ServerCallContext()
    )
    [dispatched] = await store.get_info_for_dispatch("task-1")
    assert dispatched.url.endswith("/a2a/push/s/t/a")


async def test_a_webhook_into_the_network_is_refused_at_registration():
    store = AllowlistedPushConfigStore(InMemoryPushNotificationConfigStore(), POLICY)
    with pytest.raises(InvalidParamsError):
        await store.set_info(
            "task-1",
            config("http://process-service:8300/processes/example-approval/instances"),
            ServerCallContext(),
        )


async def test_a_forbidden_webhook_already_in_the_store_is_never_dispatched():
    inner = InMemoryPushNotificationConfigStore()
    await inner.set_info(
        "task-1", config("http://169.254.169.254/"), ServerCallContext()
    )
    store = AllowlistedPushConfigStore(inner, POLICY)
    assert await store.get_info_for_dispatch("task-1") == []


TOKEN = ServiceToken("TEST_SERVICE_TOKEN")


def a_context(headers: dict[str, str]) -> ServerCallContext:
    return ServerCallContext(state={"headers": headers})


async def test_the_extended_card_needs_the_token(monkeypatch):
    monkeypatch.setenv("TEST_SERVICE_TOKEN", "service-token-value")
    modifier = TOKEN.card_modifier()
    card = AgentCard(name="x")

    assert (
        await modifier(card, a_context({"Authorization": "Bearer service-token-value"}))
        is card
    )
    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await modifier(card, a_context({"Authorization": "Bearer wrong"}))


async def test_no_configured_token_means_nobody_gets_in(monkeypatch):
    monkeypatch.delenv("TEST_SERVICE_TOKEN", raising=False)
    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await TOKEN.card_modifier()(
            AgentCard(), a_context({"Authorization": "Bearer "})
        )


async def test_the_rest_path_answers_401_and_says_how(monkeypatch):
    monkeypatch.setenv("TEST_SERVICE_TOKEN", "service-token-value")

    async def card(request):  # pragma: no cover - reached only with the token
        return JSONResponse({"name": "x"})

    app = Starlette(routes=[Route("/extendedAgentCard", card)])
    app.add_middleware(TOKEN.middleware())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        refused = await client.get("/extendedAgentCard")
        allowed = await client.get(
            "/extendedAgentCard",
            headers={"Authorization": "Bearer service-token-value"},
        )
    assert refused.status_code == 401
    assert refused.headers["WWW-Authenticate"].startswith("Bearer")
    assert allowed.status_code == 200


async def test_without_a_dsn_tasks_live_in_memory():
    from a2a.server.tasks import InMemoryTaskStore

    from platform_core.a2a_server import task_stores

    tasks, webhooks, close = task_stores("", name="knowledge")
    assert isinstance(tasks, InMemoryTaskStore)
    assert isinstance(webhooks, InMemoryPushNotificationConfigStore)
    await close()


def test_a_libpq_dsn_becomes_an_async_engine_url():
    from platform_core.a2a_server import sqlalchemy_url

    assert (
        sqlalchemy_url("postgresql://u:p@db:5432/agents")
        == "postgresql+asyncpg://u:p@db:5432/agents"
    )
    assert sqlalchemy_url("postgres://db/x") == "postgresql+asyncpg://db/x"


async def test_tasks_and_webhooks_survive_in_postgres(monkeypatch):
    import os
    import uuid

    from a2a.types import Task, TaskState, TaskStatus

    from platform_core.a2a_server import task_stores

    dsn = os.getenv("PLATFORM_TEST_POSTGRES_DSN", "")
    if not dsn:
        pytest.skip(
            "PLATFORM_TEST_POSTGRES_DSN is required: this needs a real Postgres"
        )
    name = f"t{uuid.uuid4().hex[:8]}"
    task = Task(
        id="task-1",
        context_id="ctx",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )

    tasks, webhooks, close = task_stores(dsn, name=name)
    await tasks.save(task, ServerCallContext())
    await webhooks.set_info(
        "task-1", config("http://master-agent:8000/a2a/push/x"), ServerCallContext()
    )
    await close()

    # A new process -- a new engine -- finds what the old one wrote.
    tasks, webhooks, close = task_stores(dsn, name=name)
    try:
        found = await tasks.get("task-1", ServerCallContext())
        assert found is not None
        assert found.status.state == TaskState.TASK_STATE_WORKING
        [webhook] = await webhooks.get_info_for_dispatch("task-1")
        assert webhook.url == "http://master-agent:8000/a2a/push/x"
    finally:
        await close()
