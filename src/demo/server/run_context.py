"""Lo stato che vive quanto una run, e non quanto il processo."""
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

logger = logging.getLogger(__name__)

current_run_events: contextvars.ContextVar[asyncio.Queue | None] = contextvars.ContextVar(
    "current_run_events", default=None
)
current_plan: contextvars.ContextVar[PlanStore | None] = contextvars.ContextVar(
    "current_plan", default=None
)
current_thread: contextvars.ContextVar[str] = contextvars.ContextVar("current_thread", default="")
current_pending: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "current_pending", default=None
)

StateLoader = Callable[[str], Awaitable[dict[str, Any] | None]]


def _pending_da(input_data: dict[str, Any]) -> dict[str, Any] | None:
    stato = input_data.get("state") or {}
    attesa = stato.get("subagent_pending") if isinstance(stato, dict) else None
    return attesa if isinstance(attesa, dict) and attesa else None

_FINE = object()


def plan_of_run() -> PlanStore | None:
    return current_plan.get()


def thread_of_run() -> str:
    return current_thread.get()


def pending_of_run() -> dict[str, Any] | None:
    """Il sottoagente che sta aspettando una risposta, se ce ne e' uno."""
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
    except Exception as errore:
        if queue is not None:
            await queue.put(
                SubagentErrorEvent(
                    subagent_run_id=run_id,
                    message=str(errore),
                    code=type(errore).__name__,
                )
            )
        raise
    else:
        if queue is not None:
            await queue.put(SubagentFinishedEvent(subagent_run_id=run_id))


def plan_from_state(input_data: dict[str, Any], stored: dict[str, Any] | None = None) -> PlanStore:
    if stored:
        return PlanStore(stored)
    state = input_data.get("state") or {}
    return PlanStore(state.get("plan") if isinstance(state, dict) else None)


class LabRunner(AgentFrameworkAgent):
    """Runner AG-UI che apre un contesto per ogni run.

    Il piano arriva dallo stato condiviso della richiesta e ci torna: il
    processo non ne conserva copia, cosi' due repliche non si contraddicono.
    Gli eventi dei sottoagenti si intrecciano a quelli del framework.
    """

    def __init__(self, *args: Any, state_loader: StateLoader | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._state_loader = state_loader

    def _framework_events(self, input_data: dict[str, Any]) -> AsyncGenerator[BaseEvent, None]:
        return super().run(input_data)

    async def _stato_salvato(self, input_data: dict[str, Any]) -> dict[str, Any] | None:
        thread_id = input_data.get("thread_id") or input_data.get("threadId")
        if self._state_loader is None or not thread_id:
            return None
        try:
            return await self._state_loader(str(thread_id))
        except Exception:
            logger.error(
                "Stato del thread %s non recuperato: la run riparte senza.",
                thread_id,
                exc_info=True,
            )
            return None

    async def run(self, input_data: dict[str, Any]) -> AsyncGenerator[BaseEvent, None]:
        queue: asyncio.Queue = asyncio.Queue()
        stato_salvato = await self._stato_salvato(input_data)
        plan = plan_from_state(input_data, (stato_salvato or {}).get("plan"))
        pending = (stato_salvato or {}).get("subagent_pending") or _pending_da(input_data)

        async def pompa() -> None:
            eventi = current_run_events.set(queue)
            piano = current_plan.set(plan)
            thread = current_thread.set(
                str(input_data.get("thread_id") or input_data.get("threadId") or "")
            )
            attesa = current_pending.set(pending)
            try:
                async for event in self._framework_events(input_data):
                    await queue.put(event)
            except Exception as errore:
                await queue.put(errore)
            finally:
                current_run_events.reset(eventi)
                current_plan.reset(piano)
                current_thread.reset(thread)
                current_pending.reset(attesa)
                await queue.put(_FINE)

        task = asyncio.create_task(pompa())
        try:
            while True:
                item = await queue.get()
                if item is _FINE:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
