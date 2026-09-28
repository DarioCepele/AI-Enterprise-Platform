"""Logs you can join, and traces and metrics that cross the services.

One request produces lines in several services and nothing to sew them
together. Two things fix that: every line carries the identifiers of the work
it belongs to, and every outgoing call carries the trace, so the next service
continues it instead of opening one of its own.

The exporter is optional. Without `OTEL_EXPORTER_OTLP_ENDPOINT` nothing is
exported and nothing breaks: a template that needed a collector to start would
be a template nobody runs. The standard `OTEL_*` variables (service name,
resource attributes, headers) are honoured as the OpenTelemetry SDK defines
them.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_ENDPOINT"

ContextProvider = Callable[[], Mapping[str, Any]]

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def current_trace_ids() -> dict[str, str]:
    """The trace and span of the running work, when a tracer is active."""
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - otel is an optional dependency
        return {}
    span = trace.get_current_span().get_span_context()
    if not span.is_valid:
        return {}
    return {
        "trace_id": format(span.trace_id, "032x"),
        "span_id": format(span.span_id, "016x"),
    }


def current_trace_id() -> str | None:
    """Only the trace id: what a record needs to be found again in a collector."""
    return current_trace_ids().get("trace_id")


class JsonFormatter(logging.Formatter):
    """One line, one object: service, level, message, and what joins it to a turn.

    `context` adds the identifiers a service knows about the running work -- a
    thread, an instance -- without every call site having to repeat them.
    """

    def __init__(self, service: str, context: ContextProvider | None = None) -> None:
        super().__init__()
        self._service = service
        self._context = context

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(current_trace_ids())
        if self._context is not None:
            payload.update({k: v for k, v in self._context().items() if v})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED
            }
        )
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    service: str, as_json: bool, context: ContextProvider | None = None
) -> None:
    """Structured or readable, one handler either way.

    JSON is for a log collector; a person reading `docker compose logs` wants
    the plain line, so the choice stays configuration. Uvicorn configures only
    its own loggers: without this, the application's lines would reach the
    handler of last resort, which prints only warnings.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    if as_json:
        handler.setFormatter(JsonFormatter(service, context))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def configure_telemetry(
    service: str, app: Any = None, *, agent_framework: bool = False
) -> bool:
    """Traces and metrics that survive the jump between services.

    Instrumenting httpx is what makes the trace travel: every client in the
    platform goes through it. `agent_framework=True` also turns on the
    framework's own GenAI instrumentation -- spans for model calls and tool
    invocations, token usage as metrics, following the OpenTelemetry GenAI
    semantic conventions -- without message content, which is not diagnostic
    material and may be personal data.
    """
    endpoint = os.getenv(ENDPOINT_VARIABLE, "").strip()
    if not endpoint:
        logger.info("Telemetry off: no %s configured.", ENDPOINT_VARIABLE)
        return False

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # OTEL_SERVICE_NAME wins when an operator sets it: the name in the code is
    # the default, not a decision taken for every deployment.
    name = os.getenv("OTEL_SERVICE_NAME", "").strip() or service
    resource = Resource.create({"service.name": name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
        )
    )

    HTTPXClientInstrumentor().instrument()
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    if agent_framework:
        from agent_framework.observability import enable_instrumentation

        enable_instrumentation(enable_sensitive_data=False)
    logger.info("Telemetry on, exporting traces and metrics to %s.", endpoint)
    return True
