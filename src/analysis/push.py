"""Which events are worth a push notification."""
from __future__ import annotations

import logging

from a2a.server.tasks import BasePushNotificationSender, PushNotificationEvent
from a2a.types import TaskState

logger = logging.getLogger(__name__)

WORTH_NOTIFYING = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
}


def _state_of(event: PushNotificationEvent) -> int | None:
    status = getattr(event, "status", None)
    if status is None:
        return None
    state: int = status.state
    return state


class EssentialNotifications(BasePushNotificationSender):
    """Notifies only the points where the caller has something to do.

    The default notifies every event on the queue: while streaming that is
    dozens of updates per task, all discarded by whoever receives them. What
    remains is the end of the task and the request for a clarification, the
    only moments when whoever asked for the work has to move.
    """

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        state = _state_of(event)
        if state not in WORTH_NOTIFYING:
            return
        logger.info("Push notification for task %s: state %s.", task_id[:8], state)
        await super().send_notification(task_id, event)
