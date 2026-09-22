"""A2A client written against the stable SDK."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import Client, ClientCallContext, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    GetExtendedAgentCardRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from google.protobuf.json_format import ParseDict

logger = logging.getLogger(__name__)

CARD_PATH = ".well-known/agent-card.json"

STATES = {
    TaskState.TASK_STATE_SUBMITTED: "accepted",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_CANCELED: "canceled",
    TaskState.TASK_STATE_INPUT_REQUIRED: "waiting for an answer",
    TaskState.TASK_STATE_REJECTED: "rejected",
    TaskState.TASK_STATE_AUTH_REQUIRED: "needs authentication",
}

OPEN = {TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING}
CLOSED = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


@dataclass
class Artifact:
    """A structured output of the subagent."""

    artifact_id: str
    name: str
    description: str
    text: str
    data: dict[str, Any] | None = None


@dataclass
class Progress:
    """One step of the task, as seen by whoever asked for it."""

    task_id: str
    state: str
    raw_state: int
    # The conversation the task belongs to. Answering a task that is waiting
    # means sending into **that** conversation: a message without it opens a
    # new one, and the agent refuses it as belonging somewhere else.
    context_id: str = ""
    text: str = ""
    artifact: Artifact | None = None
    question: str = ""

    @property
    def closed(self) -> bool:
        return self.raw_state in CLOSED

    @property
    def waiting_for_an_answer(self) -> bool:
        return self.raw_state == TaskState.TASK_STATE_INPUT_REQUIRED


@dataclass
class Outcome:
    """How a task ended."""

    task_id: str
    state: str
    text: str
    artifacts: list[Artifact] = field(default_factory=list)
    question: str = ""

    @property
    def succeeded(self) -> bool:
        return self.state == STATES[TaskState.TASK_STATE_COMPLETED]


async def fetch_agent_card(url: str, timeout: float = 10.0) -> AgentCard:
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(f"{url.rstrip('/')}/{CARD_PATH}")
        response.raise_for_status()
        return ParseDict(response.json(), AgentCard(), ignore_unknown_fields=True)


def _text_of(parts: Iterable[Any]) -> str:
    return "".join(part.text for part in parts if part.text)


def _artifact_of(artifact: Any) -> Artifact:
    data = None
    for part in artifact.parts:
        if part.HasField("data"):
            from google.protobuf.json_format import MessageToDict

            data = MessageToDict(part.data)
            break
    return Artifact(
        artifact_id=artifact.artifact_id,
        name=artifact.name,
        description=artifact.description,
        text=_text_of(artifact.parts),
        data=data,
    )


class A2AClient:
    """Talks to a remote agent while seeing the task's lifecycle."""

    def __init__(
        self, card: AgentCard, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._http = http_client or httpx.AsyncClient(timeout=120.0)
        self._client: Client = ClientFactory(
            ClientConfig(httpx_client=self._http, streaming=True)
        ).create(card)

    async def ask(
        self,
        text: str,
        task_id: str | None = None,
        context_id: str | None = None,
        webhook: tuple[str, str] | None = None,
    ) -> AsyncIterator[Progress]:
        """Sends a message and yields the task's progress.

        With `task_id` the message resumes a task that was waiting for an
        answer, instead of opening a new one.
        """
        message = Message(
            message_id=uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=text)],
        )
        if task_id:
            message.task_id = task_id
        if context_id:
            message.context_id = context_id

        request = SendMessageRequest(message=message)
        if webhook:
            url, token = webhook
            request.configuration.CopyFrom(
                SendMessageConfiguration(
                    task_push_notification_config=TaskPushNotificationConfig(
                        url=url, token=token
                    )
                )
            )

        current = task_id or ""
        conversation = context_id or ""
        async for response in self._client.send_message(request):
            if response.HasField("task"):
                task = response.task
                current = task.id
                conversation = task.context_id or conversation
                yield Progress(
                    task_id=task.id,
                    context_id=conversation,
                    state=STATES.get(task.status.state, "unknown"),
                    raw_state=task.status.state,
                    text=_text_of(task.status.message.parts)
                    if task.status.message.parts
                    else "",
                )
            elif response.HasField("status_update"):
                update = response.status_update
                current = update.task_id or current
                conversation = update.context_id or conversation
                update_message = update.status.message
                update_text = (
                    _text_of(update_message.parts) if update_message.parts else ""
                )
                yield Progress(
                    task_id=current,
                    context_id=conversation,
                    state=STATES.get(update.status.state, "unknown"),
                    raw_state=update.status.state,
                    text=update_text,
                    question=update_text
                    if update.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                    else "",
                )
            elif response.HasField("artifact_update"):
                artifact_update = response.artifact_update
                current = artifact_update.task_id or current
                conversation = artifact_update.context_id or conversation
                yield Progress(
                    task_id=current,
                    context_id=conversation,
                    state=STATES[TaskState.TASK_STATE_WORKING],
                    raw_state=TaskState.TASK_STATE_WORKING,
                    artifact=_artifact_of(artifact_update.artifact),
                )
            elif response.HasField("message"):
                yield Progress(
                    task_id=current,
                    context_id=conversation,
                    state=STATES[TaskState.TASK_STATE_COMPLETED],
                    raw_state=TaskState.TASK_STATE_COMPLETED,
                    text=_text_of(response.message.parts),
                )

    async def extended_card(self, token: str) -> AgentCard | None:
        """The card the agent serves only to callers that authenticate.

        The token travels as a parameter of the call and not as a requirement
        of the card: the agent is public, it is the extended view that is not.
        A caller without the right receives an error, and in that case we go on
        with the public card instead of stopping.
        """
        context = ClientCallContext(
            service_parameters={"Authorization": f"Bearer {token}"}
        )
        try:
            return await self._client.get_extended_agent_card(
                GetExtendedAgentCardRequest(), context=context
            )
        except Exception:
            logger.warning("Extended card not obtained: going on with the public one.")
            return None

    async def outcome(self, task_id: str) -> Outcome:
        """Re-reads a finished task.

        The push notification says the task is done, not what it produced: the
        result is fetched, instead of being rebuilt by accumulating
        notifications -- which would be process state.
        """
        task = await self._client.get_task(GetTaskRequest(id=task_id))
        artifacts = [_artifact_of(a) for a in task.artifacts]
        text = "\n".join(a.text for a in artifacts if a.text).strip()
        return Outcome(
            task_id=task.id,
            state=STATES.get(task.status.state, "unknown"),
            text=text,
            artifacts=artifacts,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
