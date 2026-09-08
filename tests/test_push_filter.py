"""Quali eventi escono davvero come notifica push."""
from __future__ import annotations

import pytest
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent

from knowledge.push import NotificheEssenziali


@pytest.fixture
def sender(monkeypatch):
    """Il filtro, con la spedizione vera sostituita da una lista."""
    inviati: list[str] = []

    async def base(self, task_id, event):
        inviati.append(task_id)

    monkeypatch.setattr(
        "a2a.server.tasks.BasePushNotificationSender.send_notification", base
    )
    filtro = NotificheEssenziali.__new__(NotificheEssenziali)
    filtro.inviati = inviati
    return filtro


def stato(state: TaskState) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(task_id="t1", status=TaskStatus(state=state))


@pytest.mark.asyncio
async def test_progress_does_not_travel(sender):
    await sender.send_notification("t1", stato(TaskState.TASK_STATE_WORKING))
    await sender.send_notification("t1", stato(TaskState.TASK_STATE_SUBMITTED))

    # In streaming ogni pezzo di testo e' un aggiornamento di stato: notificarli
    # tutti sono decine di POST che chi li riceve scarta comunque.
    assert sender.inviati == []


@pytest.mark.asyncio
async def test_the_end_and_the_question_travel(sender):
    await sender.send_notification("t1", stato(TaskState.TASK_STATE_COMPLETED))
    await sender.send_notification("t1", stato(TaskState.TASK_STATE_INPUT_REQUIRED))
    await sender.send_notification("t1", stato(TaskState.TASK_STATE_FAILED))

    assert sender.inviati == ["t1", "t1", "t1"]


@pytest.mark.asyncio
async def test_a_finished_task_travels(sender):
    task = Task(id="t1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

    await sender.send_notification("t1", task)

    assert sender.inviati == ["t1"]


@pytest.mark.asyncio
async def test_an_artifact_alone_does_not_travel(sender):
    # L'artefatto si va a rileggere col task: la notifica dice che c'e', non cosa dice.
    await sender.send_notification("t1", TaskArtifactUpdateEvent(task_id="t1"))

    assert sender.inviati == []
