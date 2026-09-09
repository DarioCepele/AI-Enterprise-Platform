"""The subagents' push notifications: correlation and trust."""
from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
import pytest

from demo.a2a.push import HEADER, summary_of, token_for, token_is_valid, webhook_url
from demo.chat_clients.fake import FakeStreamingChatClient
from demo.agents.master import build_master_agent
from demo.server.app import create_app

NOTIFICATION = {
    "task": {
        "id": "task-99",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"name": "briefing", "parts": [{"text": "Goroutines are lightweight."}]}],
    }
}


def test_the_token_signs_the_thread_not_the_task():
    # The webhook is registered before the task exists: a token over the task id
    # could not be computed in advance.
    assert token_for("t1") == token_for("t1")
    assert token_for("t1") != token_for("t2")


def test_a_token_of_another_thread_is_not_valid():
    assert token_is_valid("t1", token_for("t1")) is True
    assert token_is_valid("t1", token_for("t2")) is False
    assert token_is_valid("t1", None) is False


def test_the_url_carries_the_correlation():
    url = webhook_url("http://master:8000", "tenant-a", "t1")

    assert url == "http://master:8000/a2a/push/tenant-a/t1"


def test_a_notification_is_read_without_trusting_its_shape():
    assert summary_of(NOTIFICATION) == ("task-99", "TASK_STATE_COMPLETED", "Goroutines are lightweight.")
    assert summary_of({}) == ("", "", "")
    assert summary_of({"task": {"id": "x"}}) == ("x", "", "")


@pytest.fixture
def thread() -> str:
    """A thread of its own for each test.

    The notifications already seen are remembered in Redis, and remembering is
    the point: two tests sharing a thread would read each other's leftovers.
    """
    return f"t-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def app():
    return create_app(
        agent=build_master_agent(chat_client=FakeStreamingChatClient(chunks=["ok"]))
    )


async def send(app, thread_id: str, token: str | None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/a2a/push/tenant-a/{thread_id}",
            json=NOTIFICATION,
            headers={HEADER: token} if token else {},
        )


@pytest.mark.asyncio
async def test_a_signed_notification_is_accepted(app, caplog, thread):
    with caplog.at_level(logging.INFO, logger="demo.server.app"):
        response = await send(app, thread, token_for(thread))

    assert response.status_code == 200
    assert "completed task task-99" in caplog.text


@pytest.mark.asyncio
async def test_an_unsigned_notification_is_refused(app, caplog, thread):
    # An open webhook is a way to let anyone write into a conversation's
    # memory.
    with caplog.at_level(logging.WARNING, logger="demo.server.app"):
        response = await send(app, thread, None)

    assert response.status_code == 403
    assert "invalid token" in caplog.text


@pytest.mark.asyncio
async def test_a_notification_signed_for_another_thread_is_refused(app, thread):
    response = await send(app, thread, token_for("another"))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_without_a_memory_service_the_outcome_stays_in_the_logs(app, caplog, monkeypatch, thread):
    monkeypatch.setenv("DEMO_MEMORY_SERVICE_URL", "")

    with caplog.at_level(logging.WARNING, logger="demo.server.app"):
        response = await send(app, thread, token_for(thread))

    assert response.status_code == 200
    assert "stays in the logs" in caplog.text


@pytest.mark.asyncio
async def test_the_outcome_is_written_into_the_thread_memory(app, monkeypatch, thread):
    received: list[tuple[str, dict]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        import json

        received.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201, json={"seq": 1})

    monkeypatch.setenv("DEMO_MEMORY_SERVICE_URL", "http://memory")
    original = httpx.AsyncClient

    def fake(*args, **kwargs):
        # Only the client towards memory: patching them all would intercept the
        # ASGI transport the test talks to the app with.
        if kwargs.get("base_url") == "http://memory":
            kwargs["transport"] = httpx.MockTransport(transport)
        return original(*args, **kwargs)

    monkeypatch.setattr("demo.server.app.httpx.AsyncClient", fake)

    response = await send(app, thread, token_for(thread))
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert received, "nothing was written to memory"
    url, body = received[0]
    assert url.endswith(f"/threads/{thread}/messages")
    assert "Goroutines are lightweight." in body["content"]


PROGRESS = {"statusUpdate": {"taskId": "task-99", "status": {"state": "TASK_STATE_WORKING"}}}


@pytest.mark.asyncio
async def test_progress_notifications_are_ignored(app, caplog, thread):
    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="demo.server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/a2a/push/tenant-a/{thread}",
                json=PROGRESS,
                headers={HEADER: token_for(thread)},
            )

    # The subagent notifies every event: writing to memory on every progress
    # step would fill the conversation with noise.
    assert response.json() == {"state": "progress ignored"}
    assert "completed task" not in caplog.text


def test_a_token_of_a_past_window_is_refused(monkeypatch):
    monkeypatch.setenv("DEMO_PUSH_WINDOW_SECONDS", "3600")
    now = 1_800_000_000.0

    old = token_for("t1", at=now - 3 * 3600)

    # A token that never expires is a key somebody can keep: signing the window
    # too bounds how long a captured one is worth anything.
    assert token_is_valid("t1", old, at=now) is False


def test_a_token_of_the_previous_window_still_works(monkeypatch):
    monkeypatch.setenv("DEMO_PUSH_WINDOW_SECONDS", "3600")
    now = 1_800_000_000.0

    previous = token_for("t1", at=now - 3600)

    # The subagent registers the webhook when the task starts and calls back when
    # it ends: one window of slack is what makes a slow task still deliverable.
    assert token_is_valid("t1", previous, at=now) is True


@pytest.mark.asyncio
async def test_the_same_notification_is_written_once(app, monkeypatch, thread):
    received: list[tuple[str, dict]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        import json

        received.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201, json={"seq": 1})

    monkeypatch.setenv("DEMO_MEMORY_SERVICE_URL", "http://memory")
    original = httpx.AsyncClient

    def fake(*args, **kwargs):
        if kwargs.get("base_url") == "http://memory":
            kwargs["transport"] = httpx.MockTransport(transport)
        return original(*args, **kwargs)

    monkeypatch.setattr("demo.server.app.httpx.AsyncClient", fake)

    first = await send(app, thread, token_for(thread))
    second = await send(app, thread, token_for(thread))
    await asyncio.sleep(0)

    # A2A delivery is at-least-once: the same outcome arriving twice must not
    # become two lines in the conversation.
    assert first.status_code == 200
    assert second.json() == {"state": "already seen"}
    assert len(received) == 1
