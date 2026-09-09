"""The analysis agent's A2A executor, written against the stable SDK."""
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

ARTIFACT = "assessment"

# Marker the model emits when the question does not stand on its own.
ASKS = "[NEEDS-CLARIFICATION]"

MEASURING = ("measure", "compare")


class Measurements:
    """What was actually computed, read off the tool calls as they stream.

    The answer says what the numbers mean; this says which numbers there were.
    Without it the artifact would be prose about arithmetic nobody can check --
    and the point of a structured artifact is that somebody can.
    """

    def __init__(self) -> None:
        self._partials: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def observe(self, update: Any) -> None:
        for content in getattr(update, "contents", None) or []:
            kind = getattr(content, "type", "")
            call_id = getattr(content, "call_id", "") or ""
            if kind == "function_call":
                # The name arrives in the first piece and the arguments in the
                # later ones: keeping the name by call id is what lets the two
                # be put back together.
                name = getattr(content, "name", "") or ""
                if name in MEASURING:
                    self._names[call_id] = name
                if call_id not in self._names:
                    continue
                arguments = getattr(content, "arguments", None)
                if isinstance(arguments, dict):
                    self._record(call_id, arguments)
                elif isinstance(arguments, str):
                    self._partials[call_id] = self._partials.get(call_id, "") + arguments
                    self._try(call_id)
            elif kind == "function_result" and call_id in self._names:
                self._result(call_id, content)

    def _try(self, call_id: str) -> None:
        try:
            arguments = json.loads(self._partials[call_id])
        except json.JSONDecodeError:
            return
        if isinstance(arguments, dict):
            self._record(call_id, arguments)

    def _record(self, call_id: str, arguments: dict[str, Any]) -> None:
        for call in self.calls:
            if call["id"] == call_id:
                call["arguments"] = arguments
                return
        self.calls.append(
            {"id": call_id, "tool": self._names[call_id], "arguments": arguments, "result": None}
        )

    def _result(self, call_id: str, content: Any) -> None:
        text = getattr(content, "text", None)
        if text is None:
            results = getattr(content, "results", None) or []
            text = "".join(getattr(item, "text", "") or "" for item in results)
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        for call in self.calls:
            if call["id"] == call_id:
                call["result"] = parsed
                return

    def as_data(self) -> list[dict[str, Any]]:
        return [
            {"tool": call["tool"], "arguments": call["arguments"], "result": call["result"]}
            for call in self.calls
        ]


class Effort:
    """What the answer cost: model calls, and tokens in and out.

    The caller is going to compare "two agents in parallel" against "one agent
    doing both", and that comparison is worth nothing without the tokens. Every
    provider reports them at the end of each model call, so the count of those
    reports is also the number of rounds.
    """

    def __init__(self) -> None:
        self.rounds = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def observe(self, update: Any) -> None:
        for content in getattr(update, "contents", None) or []:
            if getattr(content, "type", "") != "usage":
                continue
            details = getattr(content, "usage_details", None) or {}
            self.rounds += 1
            self.input_tokens += int(details.get("input_token_count") or 0)
            self.output_tokens += int(details.get("output_token_count") or 0)

    def as_data(self) -> dict[str, int]:
        return {
            "rounds": self.rounds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def assessment(
    question: str,
    answer: str,
    measurements: list[dict[str, Any]],
    effort: dict[str, int] | None = None,
) -> list[Part]:
    """The subagent's output: text for the model, numbers for the interface."""
    data = ParseDict(
        {
            "component": "assessment",
            "question": question,
            "measurements": measurements,
            "summary": answer,
            "usage": effort or {"rounds": 0, "input_tokens": 0, "output_tokens": 0},
        },
        Value(),
    )
    return [Part(text=answer), Part(data=data)]


class AnalysisExecutor(AgentExecutor):
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
        effort = Effort()
        measured = Measurements()
        try:
            async for update in self._agent.run(question, stream=True):
                effort.observe(update)
                measured.observe(update)
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
                message=updater.new_agent_message([Part(text=f"analysis agent: {error}")])
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
                    [Part(text="analysis agent: no answer produced")]
                )
            )
            return

        await updater.add_artifact(
            assessment(question, answer, measured.as_data(), effort.as_data()),
            name=ARTIFACT,
            last_chunk=True,
        )
        await updater.complete()
        logger.info(
            "Task %s completed: %d characters, %d measurements, %d rounds, %d/%d tokens.",
            task.id,
            len(answer),
            len(measured.calls),
            effort.rounds,
            effort.input_tokens,
            effort.output_tokens,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()
        logger.info("Task %s canceled on request.", context.task_id)
