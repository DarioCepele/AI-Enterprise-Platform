"""Which events leave as a push notification, and to where."""

from __future__ import annotations

import httpx
import pytest
from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from platform_core.a2a_server import EssentialPushSender

from analysis.config import Settings
from analysis.server import create_app


@pytest.fixture
def sender(monkeypatch):
    """The filter, with the real dispatch replaced by a list."""
    sent: list[str] = []

    async def base(self, task_id, event):
        sent.append(task_id)

    monkeypatch.setattr(
        "a2a.server.tasks.BasePushNotificationSender.send_notification", base
    )
    a_filter = EssentialPushSender.__new__(EssentialPushSender)
    a_filter.sent = sent
    return a_filter


def a_status(state: TaskState) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(task_id="t1", status=TaskStatus(state=state))


async def test_progress_does_not_travel(sender):
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_WORKING))
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_SUBMITTED))

    # While streaming, every piece of text is a status update: notifying all of
    # them is dozens of POSTs that the receiver discards anyway.
    assert sender.sent == []


async def test_the_end_and_the_question_travel(sender):
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_COMPLETED))
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_INPUT_REQUIRED))
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_FAILED))

    assert sender.sent == ["t1", "t1", "t1"]


async def test_a_finished_task_travels(sender):
    task = Task(id="t1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

    await sender.send_notification("t1", task)

    assert sender.sent == ["t1"]


async def test_an_artifact_alone_does_not_travel(sender):
    # The artifact is re-read with the task: the notification says it exists,
    # not what it says.
    await sender.send_notification("t1", TaskArtifactUpdateEvent(task_id="t1"))

    assert sender.sent == []


class SilentAgent:
    """An agent that answers nothing: the request must fail before it matters."""

    def run(self, question: str, stream: bool = False):
        async def _stream():
            if False:  # pragma: no cover - an empty async generator
                yield None

        return _stream()


async def send_with_webhook(url: str) -> dict:
    app = create_app(
        agent=SilentAgent(),  # type: ignore[arg-type]
        settings=Settings(push_allowed_urls="http://master-agent:8000/a2a/push/"),
    )
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
            "configuration": {
                "taskPushNotificationConfig": {"url": url, "token": "tok"}
            },
        },
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/", json=request, headers={"A2A-Version": "1.0"})
    return response.json()


async def test_a_webhook_into_the_internal_network_is_refused():
    # Whoever sends a task chooses the webhook: without the allowlist, this
    # agent would POST wherever it is told -- here, to start a process.
    answer = await send_with_webhook(
        "http://process-service:8300/processes/example-approval/instances"
    )
    assert "error" in answer
    assert "not allowed" in answer["error"]["message"]


async def test_a_webhook_to_an_allowed_receiver_is_accepted():
    answer = await send_with_webhook("http://master-agent:8000/a2a/push/s/t/analysis")
    assert "error" not in answer
