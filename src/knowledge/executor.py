"""The knowledge agent's A2A executor, written against the stable SDK."""
from __future__ import annotations

import json
import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_task_from_user_message
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState
from agent_framework import Agent
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

logger = logging.getLogger(__name__)

ARTIFACT = "briefing"

# Marker the model emits when the question does not stand on its own.
ASKS = "[NEEDS-CLARIFICATION]"


class DocumentReads:
    """Which documents the agent read, from arguments that arrive as deltas.

    The model streams a tool call in several pieces, and the pieces do not look
    alike: the first carries the tool **name** and empty arguments, the later
    ones carry the **arguments** as deltas and no name. Filtering by name on
    every piece drops exactly the ones holding the answer -- measured, not
    imagined.
    """

    def __init__(self) -> None:
        self._partials: dict[str, str] = {}
        self._ours: set[str] = set()
        self.documents: list[str] = []

    def observe(self, update: Any) -> None:
        for content in getattr(update, "contents", None) or []:
            if getattr(content, "type", "") != "function_call":
                continue
            call_id = getattr(content, "call_id", "") or ""
            tool_name = getattr(content, "name", "") or ""
            if tool_name == "read_document":
                self._ours.add(call_id)
            elif tool_name:
                continue
            if call_id not in self._ours:
                continue
            arguments = getattr(content, "arguments", None)
            if isinstance(arguments, dict):
                self._record(arguments.get("name"))
                continue
            if isinstance(arguments, str):
                self._partials[call_id] = self._partials.get(call_id, "") + arguments
                self._try(self._partials[call_id])

    def _try(self, raw: str) -> None:
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(arguments, dict):
            self._record(arguments.get("name"))

    def _record(self, name: Any) -> None:
        if isinstance(name, str) and name and name not in self.documents:
            self.documents.append(name)


def briefing(question: str, answer: str, documents: list[str]) -> list[Part]:
    """The subagent's output: text for the model, data for the interface."""
    data = ParseDict(
        {
            "component": "briefing",
            "question": question,
            "documents": documents,
            "summary": answer,
        },
        Value(),
    )
    return [Part(text=answer), Part(data=data)]


class KnowledgeExecutor(AgentExecutor):
    """Turns an A2A request into an agent run, and back.

    Hand-written instead of using the beta glue for two reasons: it takes a
    beta package off the load-bearing path, and it allows emitting an artifact
    **with a name and data** instead of a sequence of anonymous chunks.
    """

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, context.context_id)
        question = context.get_user_input()

        await updater.submit()
        await updater.start_work()

        pieces: list[str] = []
        reads = DocumentReads()
        try:
            async for update in self._agent.run(question, stream=True):
                reads.observe(update)
                text = getattr(update, "text", None)
                if text:
                    pieces.append(text)
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message([Part(text=text)]),
                    )
        except Exception as error:
            logger.error("Run failed for '%s'.", question, exc_info=True)
            await updater.failed(
                message=updater.new_agent_message([Part(text=f"knowledge agent: {error}")])
            )
            return

        answer = "".join(pieces).strip()

        if answer.startswith(ASKS):
            question_back = answer[len(ASKS) :].strip() or "Puoi precisare la richiesta?"
            await updater.requires_input(
                message=updater.new_agent_message([Part(text=question_back)])
            )
            logger.info("Task %s is waiting for a clarification.", task.id)
            return

        if not answer:
            await updater.failed(
                message=updater.new_agent_message(
                    [Part(text="knowledge agent: no answer produced")]
                )
            )
            return

        await updater.add_artifact(
            briefing(question, answer, reads.documents),
            name=ARTIFACT,
            last_chunk=True,
        )
        await updater.complete()
        logger.info(
            "Task %s completed: %d characters, documents %s.",
            task.id,
            len(answer),
            ", ".join(reads.documents) or "none",
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()
        logger.info("Task %s canceled on request.", context.task_id)
