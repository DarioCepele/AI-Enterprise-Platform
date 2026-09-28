"""The subagents' push notifications: correlation and trust."""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
import pytest

from master_agent.a2a.push import (
    HEADER,
    summary_of,
    token_for,
    token_is_valid,
    webhook_url,
)
from master_agent.agents.master import build_master_agent
from master_agent.chat_clients.fake import FakeStreamingChatClient
from master_agent.server.app import create_app

NOTIFICATION = {
    "task": {
        "id": "task-99",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [
            {"name": "briefing", "parts": [{"text": "Goroutines are lightweight."}]}
        ],
    }
}

SCOPE = "tenant-a"
AGENT = "knowledge"


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    """Every test signs: a receiver without a key accepts nothing."""
    monkeypatch.setenv("MASTER_PUSH_SECRET", "a-test-push-secret-long-enough")
    monkeypatch.setenv(
        "MASTER_SUBAGENTS", '[{"name": "knowledge", "url": "http://knowledge:8200/"}]'
    )


def test_the_token_signs_scope_thread_and_agent():
    token = token_for(SCOPE, "t1", AGENT)

    assert token == token_for(SCOPE, "t1", AGENT)
    assert token != token_for(SCOPE, "t2", AGENT)
    assert token != token_for("tenant-b", "t1", AGENT)
    assert token != token_for(SCOPE, "t1", "analysis")


def test_a_token_of_another_thread_is_not_valid():
    assert token_is_valid(SCOPE, "t1", AGENT, token_for(SCOPE, "t1", AGENT)) is True
    assert token_is_valid(SCOPE, "t1", AGENT, token_for(SCOPE, "t2", AGENT)) is False
    assert token_is_valid(SCOPE, "t1", AGENT, None) is False


def test_without_a_secret_nothing_is_valid(monkeypatch):
    token = token_for(SCOPE, "t1", AGENT)
    monkeypatch.setenv("MASTER_PUSH_SECRET", "")

    assert token_is_valid(SCOPE, "t1", AGENT, token) is False


def test_the_url_carries_the_correlation():
    url = webhook_url("http://master:8000", SCOPE, "t1", AGENT)

    assert url == "http://master:8000/a2a/push/tenant-a/t1/knowledge"


def test_a_notification_is_read_without_trusting_its_shape():
    assert summary_of(NOTIFICATION) == (
        "task-99",
        "TASK_STATE_COMPLETED",
        "Goroutines are lightweight.",
    )
    assert summary_of({}) == ("", "", "")
    assert summary_of({"task": {"id": "x"}}) == ("x", "", "")


@pytest.fixture
def thread() -> str:
    """A thread of its own for each test.

    The notifications already seen are remembered, and remembering is the
    point: two tests sharing a thread would read each other's leftovers.
    """
    return f"t-{uuid.uuid4().hex[:12]}"


def make_app():
    # These tests are about the endpoint's own rules, so they run on the
    # in-process memory of seen notifications: the shared table only exists
    # after the lifespan has migrated it, which ASGITransport never runs.
    # `test_log_stream.py` covers the table.
    return create_app(
        agent=build_master_agent(chat_client=FakeStreamingChatClient(chunks=["ok"]))
    )


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")
    return make_app()


async def send(
    app, thread_id: str, token: str | None, *, scope: str = SCOPE, body=NOTIFICATION
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/a2a/push/{scope}/{thread_id}/{AGENT}",
            json=body,
            headers={HEADER: token} if token else {},
        )


@pytest.mark.asyncio
async def test_a_signed_notification_is_accepted(app, caplog, thread):
    with caplog.at_level(logging.INFO, logger="master_agent.server.app"):
        response = await send(app, thread, token_for(SCOPE, thread, AGENT))

    assert response.status_code == 200
    assert "completed task task-99" in caplog.text


@pytest.mark.asyncio
async def test_an_unsigned_notification_is_refused(app, caplog, thread):
    # An open webhook is a way to let anyone write into a conversation's
    # memory.
    with caplog.at_level(logging.WARNING, logger="master_agent.server.app"):
        response = await send(app, thread, None)

    assert response.status_code == 403
    assert "invalid token" in caplog.text


@pytest.mark.asyncio
async def test_a_notification_signed_for_another_thread_is_refused(app, thread):
    response = await send(app, thread, token_for(SCOPE, "another", AGENT))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_token_cannot_be_replayed_into_another_scope(app, thread):
    response = await send(
        app, thread, token_for(SCOPE, thread, AGENT), scope="tenant-b"
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_body_of_an_unsigned_notification_is_never_read(app, thread):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/a2a/push/{SCOPE}/{thread}/{AGENT}",
            content=b"not json at all",
            headers={"content-type": "application/json"},
        )

    # Refused on the token, before anything was parsed.
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_without_a_memory_service_the_outcome_stays_in_the_logs(
    caplog, monkeypatch, thread
):
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")
    monkeypatch.setenv("MASTER_MEMORY_SERVICE_URL", "")
    app = make_app()

    with caplog.at_level(logging.WARNING, logger="master_agent.server.app"):
        response = await send(app, thread, token_for(SCOPE, thread, AGENT))

    assert response.status_code == 200
    assert "stays in the logs" in caplog.text


def recording_memory(monkeypatch) -> list[tuple[str, dict]]:
    received: list[tuple[str, dict]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        import json

        received.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201, json={"seq": 1})

    original = httpx.AsyncClient

    def fake(*args, **kwargs):
        # Only the client towards memory: patching them all would intercept the
        # ASGI transport the test talks to the app with.
        if kwargs.get("base_url") == "http://memory":
            kwargs["transport"] = httpx.MockTransport(transport)
        return original(*args, **kwargs)

    monkeypatch.setattr("master_agent.server.app.httpx.AsyncClient", fake)
    return received


@pytest.mark.asyncio
async def test_the_outcome_is_written_into_the_thread_memory(monkeypatch, thread):
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")
    monkeypatch.setenv("MASTER_MEMORY_SERVICE_URL", "http://memory")
    received = recording_memory(monkeypatch)
    app = make_app()

    response = await send(app, thread, token_for(SCOPE, thread, AGENT))
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert received, "nothing was written to memory"
    url, body = received[0]
    assert url.endswith(f"/threads/{thread}/messages")
    assert "Goroutines are lightweight." in body["content"]
    # Quoted as another agent's answer, and said to be data.
    assert "knowledge agent answered" in body["content"]
    assert "not instructions" in body["content"]


PROGRESS = {
    "statusUpdate": {"taskId": "task-99", "status": {"state": "TASK_STATE_WORKING"}}
}


@pytest.mark.asyncio
async def test_progress_notifications_are_ignored(app, caplog, thread):
    with caplog.at_level(logging.INFO, logger="master_agent.server.app"):
        response = await send(
            app, thread, token_for(SCOPE, thread, AGENT), body=PROGRESS
        )

    # The subagent notifies every event: writing to memory on every progress
    # step would fill the conversation with noise.
    assert response.json() == {"state": "progress ignored"}
    assert "completed task" not in caplog.text


def test_a_token_of_a_past_window_is_refused(monkeypatch):
    monkeypatch.setenv("MASTER_PUSH_WINDOW_SECONDS", "3600")
    now = 1_800_000_000.0

    old = token_for(SCOPE, "t1", AGENT, at=now - 3 * 3600)

    # A token that never expires is a key somebody can keep: signing the window
    # too bounds how long a captured one is worth anything.
    assert token_is_valid(SCOPE, "t1", AGENT, old, at=now) is False


def test_a_token_of_the_previous_window_still_works(monkeypatch):
    monkeypatch.setenv("MASTER_PUSH_WINDOW_SECONDS", "3600")
    now = 1_800_000_000.0

    previous = token_for(SCOPE, "t1", AGENT, at=now - 3600)

    # The subagent registers the webhook when the task starts and calls back when
    # it ends: one window of slack is what makes a slow task still deliverable.
    assert token_is_valid(SCOPE, "t1", AGENT, previous, at=now) is True


@pytest.mark.asyncio
async def test_the_same_notification_is_written_once(monkeypatch, thread):
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")
    monkeypatch.setenv("MASTER_MEMORY_SERVICE_URL", "http://memory")
    received = recording_memory(monkeypatch)
    app = make_app()

    first = await send(app, thread, token_for(SCOPE, thread, AGENT))
    second = await send(app, thread, token_for(SCOPE, thread, AGENT))
    await asyncio.sleep(0)

    # A2A delivery is at-least-once: the same outcome arriving twice must not
    # become two lines in the conversation.
    assert first.status_code == 200
    assert second.json() == {"state": "already seen"}
    assert len(received) == 1
