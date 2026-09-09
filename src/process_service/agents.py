"""Talking to a remote agent over A2A, and being told when it is done.

A step that delegates to an agent can take minutes or hours. Holding the request
open for that long is how a process becomes fragile: the task is started, the
instance suspends, and the agent calls back on a signed webhook when it has
finished. That is what A2A's task lifecycle and push notifications are for, and
using them for anything shorter would be building a second one.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from google.protobuf.json_format import MessageToDict, ParseDict

from .config import get_settings

logger = logging.getLogger(__name__)

CARD_PATH = ".well-known/agent-card.json"
TOKEN_HEADER = "X-A2A-Notification-Token"

TERMINAL = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}
NEEDS_INPUT = "TASK_STATE_INPUT_REQUIRED"


def _secret() -> bytes:
    return get_settings().push_secret.encode()


def token_for(instance_id: str, step_id: str) -> str:
    """Signs the pair the webhook is about, so a token is good for one step only."""
    payload = f"{instance_id}:{step_id}".encode()
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def token_is_valid(instance_id: str, step_id: str, received: str | None) -> bool:
    if not received:
        return False
    return hmac.compare_digest(token_for(instance_id, step_id), received)


def webhook_url(base: str, scope: str, instance_id: str, step_id: str) -> str:
    """The correlation is in the URL: whoever receives it knows which step it is."""
    return f"{base.rstrip('/')}/a2a/push/{scope}/{instance_id}/{step_id}"


def summary_of(notification: dict[str, Any]) -> tuple[str, str, str]:
    """Reads (task_id, state, text) from a notification without trusting its shape.

    The agent notifies one event at a time and in camelCase: sometimes a whole
    task, sometimes a status update, sometimes an artifact. Everything is
    accepted here, and the caller decides what to ignore.
    """
    task = notification.get("task") or {}
    status_update = notification.get("statusUpdate") or notification.get("status_update") or {}
    artifact_update = (
        notification.get("artifactUpdate") or notification.get("artifact_update") or {}
    )

    task_id = str(
        task.get("id")
        or status_update.get("taskId")
        or status_update.get("task_id")
        or artifact_update.get("taskId")
        or artifact_update.get("task_id")
        or ""
    )
    state = str(
        (task.get("status") or {}).get("state")
        or (status_update.get("status") or {}).get("state")
        or ""
    )

    artifacts = list(task.get("artifacts") or [])
    if artifact_update.get("artifact"):
        artifacts.append(artifact_update["artifact"])
    parts = [
        part["text"]
        for artifact in artifacts
        for part in artifact.get("parts") or []
        if isinstance(part.get("text"), str)
    ]
    if not parts:
        message = (status_update.get("status") or {}).get("message") or {}
        parts = [
            part["text"] for part in message.get("parts") or [] if isinstance(part.get("text"), str)
        ]
    return task_id, state, "".join(parts).strip()


def _usage_of(data: dict[str, Any]) -> dict[str, int]:
    """What the agent said the answer cost, when it says so.

    An agent that does not report it is not an error: the field is part of the
    artifacts of this laboratory, not of the A2A protocol, and a fork can plug
    in an agent that knows nothing about it.
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return {}
    counted = {}
    for key, value in usage.items():
        # Numbers cross protobuf as doubles: 2 arrives as 2.0, and a filter that
        # only accepted digits threw the whole count away.
        try:
            counted[str(key)] = int(float(value))
        except (TypeError, ValueError):
            continue
    return counted


async def fetch_card(url: str, timeout: float = 10.0) -> AgentCard:
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(f"{url.rstrip('/')}/{CARD_PATH}")
        response.raise_for_status()
        return ParseDict(response.json(), AgentCard(), ignore_unknown_fields=True)


class AgentGateway:
    """The remote agents a process may delegate to, by name."""

    def __init__(self, agents: dict[str, str], public_url: str) -> None:
        self._urls = agents
        self._public_url = public_url
        self._clients: dict[str, Client] = {}

    def knows(self, name: str) -> bool:
        return name in self._urls

    def known(self) -> list[str]:
        return sorted(self._urls)

    async def _client(self, name: str) -> Client:
        if name not in self._clients:
            card = await fetch_card(self._urls[name])
            self._clients[name] = ClientFactory(
                ClientConfig(httpx_client=httpx.AsyncClient(timeout=60.0), streaming=False)
            ).create(card)
        return self._clients[name]

    async def _send(
        self, *, agent: str, text: str, scope: str, instance_id: str, step_id: str,
        task_id: str = "",
    ) -> dict[str, Any]:
        """Sends a message and returns as soon as the agent has taken it.

        The answer does not come back through this call: it arrives on the
        webhook, which is what lets the instance suspend instead of waiting.
        """
        if not self.knows(agent):
            raise KeyError(f"agent '{agent}' is not configured. Known: {', '.join(self.known())}")

        client = await self._client(agent)
        message = Message(message_id=uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)])
        if task_id:
            message.task_id = task_id
        request = SendMessageRequest(message=message)
        request.configuration.CopyFrom(
            SendMessageConfiguration(
                task_push_notification_config=TaskPushNotificationConfig(
                    url=webhook_url(self._public_url, scope, instance_id, step_id),
                    token=token_for(instance_id, step_id),
                )
            )
        )

        state = ""
        async for response in client.send_message(request):
            if response.HasField("task"):
                task_id = response.task.id
                state = TaskState.Name(response.task.status.state)
            elif response.HasField("status_update"):
                task_id = response.status_update.task_id or task_id
                state = TaskState.Name(response.status_update.status.state)
        logger.info(
            "Instance %s step %s: told %s, task %s (%s).",
            instance_id,
            step_id,
            agent,
            task_id[:8] or "?",
            state or "unknown",
        )
        return {"task_id": task_id, "state": state}

    async def ask(
        self, *, agent: str, question: str, scope: str, instance_id: str, step_id: str
    ) -> dict[str, Any]:
        """Starts a task and returns as soon as it is accepted."""
        return await self._send(
            agent=agent, text=question, scope=scope, instance_id=instance_id, step_id=step_id
        )

    async def result_of(self, *, agent: str, task_id: str) -> dict[str, Any]:
        """Reads the task itself, because a notification is a signal, not the answer.

        A push notification says a task is done; the text can be in artifacts
        that were notified separately, or filtered out on the way. The task is
        the one place where the whole answer is -- and the only place where what
        the answer cost is written down.
        """
        client = await self._client(agent)
        task = await client.get_task(GetTaskRequest(id=task_id))
        pieces = []
        usage: dict[str, int] = {}
        for artifact in task.artifacts:
            for part in artifact.parts:
                if part.HasField("text"):
                    pieces.append(part.text)
                elif part.HasField("data"):
                    usage = usage or _usage_of(MessageToDict(part.data))
        if not pieces and task.status.HasField("message"):
            pieces = [part.text for part in task.status.message.parts if part.HasField("text")]
        return {"text": "".join(pieces).strip(), "usage": usage}

    async def reply(
        self, *, agent: str, answer: str, scope: str, instance_id: str, step_id: str, task_id: str
    ) -> dict[str, Any]:
        """Answers a clarification inside the task that asked for it.

        Sending it as a new task would throw away what the agent had already
        worked out, and would ask the same question again.
        """
        return await self._send(
            agent=agent, text=answer, scope=scope, instance_id=instance_id,
            step_id=step_id, task_id=task_id,
        )
