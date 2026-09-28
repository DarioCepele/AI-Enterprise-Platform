"""Logs that name the instance they belong to, and traces that cross services.

The generic part -- JSON lines, OpenTelemetry export -- is the platform's
(`platform_core.observability`). What is this service's own is the instance:
every line written while a step runs carries it, so a log line and an instance
can find each other without anybody remembering to say which one they were
talking about.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from platform_core import observability

_INSTANCE: ContextVar[str | None] = ContextVar("instance_id", default=None)


@contextmanager
def working_on(instance_id: str) -> Iterator[None]:
    token = _INSTANCE.set(instance_id)
    try:
        yield
    finally:
        _INSTANCE.reset(token)


def current_trace() -> str | None:
    """The trace this work belongs to, when there is a collector listening."""
    return observability.current_trace_id()


def _context() -> dict[str, str | None]:
    return {"instance_id": _INSTANCE.get()}


class JsonFormatter(observability.JsonFormatter):
    """The platform's JSON line, with the instance of the running step in it."""

    def __init__(self, service: str) -> None:
        super().__init__(service, context=_context)


def configure_logging(service: str, as_json: bool) -> None:
    observability.configure_logging(service, as_json, context=_context)


def configure_telemetry(service: str, app: Any = None) -> bool:
    # The open goals run Agent Framework's orchestration: its GenAI spans and
    # token metrics come with the rest.
    return observability.configure_telemetry(service, app, agent_framework=True)
