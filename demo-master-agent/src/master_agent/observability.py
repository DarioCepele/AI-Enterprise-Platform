"""Logs that name the thread they belong to, and traces that cross services.

The generic part -- JSON lines, OpenTelemetry export, Agent Framework's GenAI
instrumentation -- is the platform's (`platform_core.observability`). What is
this service's own is the thread: every line written during a run carries it,
so a turn's lines can be found again in a collector.
"""

from __future__ import annotations

from typing import Any

from platform_core import observability

from .server.run_context import current_thread

ENDPOINT_VARIABLE = observability.ENDPOINT_VARIABLE


def _context() -> dict[str, str]:
    return {"thread_id": current_thread.get()}


class JsonFormatter(observability.JsonFormatter):
    """The platform's JSON line, with the thread of the running turn in it."""

    def __init__(self, service: str) -> None:
        super().__init__(service, context=_context)


def configure_logging(service: str, as_json: bool) -> None:
    observability.configure_logging(service, as_json, context=_context)


def configure_telemetry(service: str, app: Any = None) -> bool:
    """Traces and metrics, with the agent's own GenAI spans and token usage."""
    return observability.configure_telemetry(service, app, agent_framework=True)


# The name the service used before the platform module existed.
configure_tracing = configure_telemetry
