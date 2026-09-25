"""The state that lives as long as a run, and not as long as the process."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from ag_ui.core.events import (
    BaseEvent,
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
)
from agent_framework_ag_ui import AgentFrameworkAgent

from ..plan import PlanStore
from .attachments import annotate_video_audio_attachments

logger = logging.getLogger(__name__)

current_run_events: contextvars.ContextVar[asyncio.Queue | None] = (
    contextvars.ContextVar("current_run_events", default=None)
)
current_plan: contextvars.ContextVar[PlanStore | None] = contextvars.ContextVar(
    "current_plan", default=None
)
current_thread: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_thread", default=""
)
current_pending: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "current_pending", default=None
)

StateLoader = Callable[[str], Awaitable[dict[str, Any] | None]]


def _pending_from(input_data: dict[str, Any]) -> dict[str, Any] | None:
    state = input_data.get("state") or {}
    waiting = state.get("subagent_pending") if isinstance(state, dict) else None
    return waiting if isinstance(waiting, dict) and waiting else None


_END = object()


def plan_of_run() -> PlanStore | None:
    return current_plan.get()


def thread_of_run() -> str:
    return current_thread.get()


def pending_of_run() -> dict[str, Any] | None:
    """The subagent waiting for an answer, if there is one."""
    return current_pending.get()


@asynccontextmanager
async def subagent_run(
    name: str,
    description: str | None = None,
    parent_tool_call_id: str | None = None,
) -> AsyncGenerator[str, None]:
    queue = current_run_events.get()
    run_id = uuid4().hex
    if queue is not None:
        await queue.put(
            SubagentStartedEvent(
                subagent_run_id=run_id,
                name=name,
                description=description,
                parent_tool_call_id=parent_tool_call_id,
            )
        )
    try:
        yield run_id
    except Exception as error:
        if queue is not None:
            await queue.put(
                SubagentErrorEvent(
                    subagent_run_id=run_id,
                    message=str(error),
                    code=type(error).__name__,
                )
            )
        raise
    else:
        if queue is not None:
            await queue.put(SubagentFinishedEvent(subagent_run_id=run_id))


def plan_from_state(
    input_data: dict[str, Any], stored: dict[str, Any] | None = None
) -> PlanStore:
    if stored:
        return PlanStore(stored)
    state = input_data.get("state") or {}
    return PlanStore(state.get("plan") if isinstance(state, dict) else None)


class LabRunner(AgentFrameworkAgent):
    """An AG-UI runner that opens a context for every run.

    The plan comes from the request's shared state and goes back into it: the
    process keeps no copy, so two replicas cannot contradict each other.
    Subagent events are interleaved with the framework's own.
    """

    def __init__(
        self, *args: Any, state_loader: StateLoader | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._state_loader = state_loader

    def _framework_events(
        self, input_data: dict[str, Any]
    ) -> AsyncGenerator[BaseEvent, None]:
        return super().run(input_data)

    async def _stored_state(self, input_data: dict[str, Any]) -> dict[str, Any] | None:
        thread_id = input_data.get("thread_id") or input_data.get("threadId")
        if self._state_loader is None or not thread_id:
            return None
        try:
            return await self._state_loader(str(thread_id))
        except Exception:
            logger.error(
                "State of thread %s not recovered: the run starts without it.",
                thread_id,
                exc_info=True,
            )
            return None

    async def run(self, input_data: dict[str, Any]) -> AsyncGenerator[BaseEvent, None]:
        raw_messages = input_data.get("messages")
        if isinstance(raw_messages, list):
            # A video/audio attachment survives as raw multimodal Content once the
            # framework's AG-UI adapter converts it - but only if the underlying
            # chat client knows what to do with a video/*-or-audio/* media type,
            # which most chat-completions APIs do not. Exposing the URL as plain
            # text too means the model deciding which tools to call can always
            # read and reuse it, regardless of what the client does with the raw
            # media content.
            input_data = {
                **input_data,
                "messages": annotate_video_audio_attachments(raw_messages),
            }

        queue: asyncio.Queue = asyncio.Queue()
        stored_state = await self._stored_state(input_data)
        plan = plan_from_state(input_data, (stored_state or {}).get("plan"))
        pending = (stored_state or {}).get("subagent_pending") or _pending_from(
            input_data
        )

        async def pump() -> None:
            events_token = current_run_events.set(queue)
            plan_token = current_plan.set(plan)
            thread_token = current_thread.set(
                str(input_data.get("thread_id") or input_data.get("threadId") or "")
            )
            pending_token = current_pending.set(pending)
            try:
                async for event in self._framework_events(input_data):
                    await queue.put(event)
            except Exception as error:
                await queue.put(error)
            finally:
                current_run_events.reset(events_token)
                current_plan.reset(plan_token)
                current_thread.reset(thread_token)
                current_pending.reset(pending_token)
                await queue.put(_END)

        task = asyncio.create_task(pump())
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
