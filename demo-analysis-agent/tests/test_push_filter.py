"""Which events actually leave as a push notification."""
from __future__ import annotations

import pytest
from a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from analysis.push import EssentialNotifications


@pytest.fixture
def sender(monkeypatch):
    """The filter, with the real dispatch replaced by a list."""
    sent: list[str] = []

    async def base(self, task_id, event):
        sent.append(task_id)

    monkeypatch.setattr(
        "a2a.server.tasks.BasePushNotificationSender.send_notification", base
    )
    a_filter = EssentialNotifications.__new__(EssentialNotifications)
    a_filter.sent = sent
    return a_filter


def a_status(state: TaskState) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(task_id="t1", status=TaskStatus(state=state))


@pytest.mark.asyncio
async def test_progress_does_not_travel(sender):
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_WORKING))
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_SUBMITTED))

    # While streaming, every piece of text is a status update: notifying all of
    # them is dozens of POSTs that the receiver discards anyway.
    assert sender.sent == []


@pytest.mark.asyncio
async def test_the_end_and_the_question_travel(sender):
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_COMPLETED))
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_INPUT_REQUIRED))
    await sender.send_notification("t1", a_status(TaskState.TASK_STATE_FAILED))

    assert sender.sent == ["t1", "t1", "t1"]


@pytest.mark.asyncio
async def test_a_finished_task_travels(sender):
    task = Task(id="t1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

    await sender.send_notification("t1", task)

    assert sender.sent == ["t1"]


@pytest.mark.asyncio
async def test_an_artifact_alone_does_not_travel(sender):
    # The artifact is re-read with the task: the notification says it exists,
    # not what it says.
    await sender.send_notification("t1", TaskArtifactUpdateEvent(task_id="t1"))

    assert sender.sent == []
