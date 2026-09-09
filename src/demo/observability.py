"""Logs you can join, and traces that cross the three services.

One question a turn produces three sets of lines -- master, subagent, memory --
and nothing to sew them together. Two things fix that: every line carries the
identifiers of the turn it belongs to, and every outgoing call carries the
trace so the next service keeps the same one.

The exporter is optional. Without `OTEL_EXPORTER_OTLP_ENDPOINT` nothing is
exported and nothing breaks: a template that needed a collector to start would
be a template nobody runs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .server.run_context import current_thread

logger = logging.getLogger(__name__)

ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_ENDPOINT"

RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """One line, one object: service, level, message, and what joins it to a turn."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }

        thread_id = current_thread.get()
        if thread_id:
            payload["thread_id"] = thread_id

        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            payload["trace_id"] = format(span.trace_id, "032x")
            payload["span_id"] = format(span.span_id, "016x")

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in RESERVED}
        )
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service: str, as_json: bool) -> None:
    """Structured or readable, one handler either way.

    JSON is for a log collector; a person reading `docker compose logs` wants
    the plain line, so the choice stays configuration.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    if as_json:
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def configure_tracing(service: str, app: Any = None) -> bool:
    """Traces that survive the jump between services, when a collector is there.

    Instrumenting httpx is what makes the trace travel: the A2A client and the
    memory client both go through it, so the subagent and the memory service
    continue the trace that started with the request instead of opening two of
    their own.
    """
    endpoint = os.getenv(ENDPOINT_VARIABLE, "").strip()
    if not endpoint:
        logger.info("Tracing off: no %s configured.", ENDPOINT_VARIABLE)
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    HTTPXClientInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    logger.info("Tracing on, exporting to %s.", endpoint)
    return True
