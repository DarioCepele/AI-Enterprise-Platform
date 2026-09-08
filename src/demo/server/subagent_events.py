"""Eventi SUBAGENT_* sullo stream AG-UI."""
from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import AsyncGenerator
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

logger = logging.getLogger(__name__)

current_run_events: contextvars.ContextVar[asyncio.Queue | None] = contextvars.ContextVar(
    "current_run_events", default=None
)

_FINE = object()


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


class SubagentEventRelay(AgentFrameworkAgent):
    """Runner AG-UI che intreccia gli eventi dei sottoagenti a quelli della run."""

    def _framework_events(self, input_data: dict[str, Any]) -> AsyncGenerator[BaseEvent, None]:
        return super().run(input_data)

    async def run(self, input_data: dict[str, Any]) -> AsyncGenerator[BaseEvent, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async def pompa() -> None:
            token = current_run_events.set(queue)
            try:
                async for event in self._framework_events(input_data):
                    await queue.put(event)
            except Exception as errore:
                await queue.put(errore)
            finally:
                current_run_events.reset(token)
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
