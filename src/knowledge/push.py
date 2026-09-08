"""Quali eventi meritano una notifica push."""
from __future__ import annotations

import logging

from a2a.server.tasks import BasePushNotificationSender
from a2a.types import TaskState

logger = logging.getLogger(__name__)

DA_NOTIFICARE = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
}


def _stato(evento) -> int | None:
    for campo in ("status", ):
        if hasattr(evento, campo):
            return getattr(evento, campo).state
    return None


class NotificheEssenziali(BasePushNotificationSender):
    """Notifica solo i punti in cui il chiamante ha qualcosa da fare.

    Il default notifica ogni evento della coda: in streaming sono decine di
    aggiornamenti per task, tutti scartati da chi li riceve. Restano la fine
    del task e la richiesta di chiarimento, che sono gli unici momenti in cui
    chi ha chiesto il lavoro deve muoversi.
    """

    async def send_notification(self, task_id: str, event) -> None:
        stato = _stato(event)
        if stato not in DA_NOTIFICARE:
            return
        logger.info("Notifica push per il task %s: stato %s.", task_id[:8], stato)
        await super().send_notification(task_id, event)
