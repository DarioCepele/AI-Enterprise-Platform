"""What a log line says, and what it takes to join three services."""

from __future__ import annotations

import json
import logging

import pytest

from master_agent.observability import JsonFormatter, configure_tracing
from master_agent.server.run_context import current_thread


def a_record(message: str = "plan written", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        "master_agent.tools", logging.INFO, __file__, 1, message, (), None
    )
    record.__dict__.update(extra)
    return record


def test_a_line_is_an_object_with_the_service_on_it():
    line = json.loads(JsonFormatter("master").format(a_record()))

    assert line["service"] == "master"
    assert line["level"] == "INFO"
    assert line["message"] == "plan written"
    assert line["logger"] == "master_agent.tools"


def test_the_line_carries_the_thread_of_the_turn():
    token = current_thread.set("t-42")
    try:
        line = json.loads(JsonFormatter("master").format(a_record()))
    finally:
        current_thread.reset(token)

    # Without it, three services produce three sets of lines and nothing to sew
    # them together.
    assert line["thread_id"] == "t-42"


def test_outside_a_turn_there_is_no_thread():
    assert "thread_id" not in json.loads(JsonFormatter("master").format(a_record()))


def test_extra_fields_travel_as_fields_not_as_prose():
    line = json.loads(JsonFormatter("master").format(a_record(task_id="4bcf45a1")))

    assert line["task_id"] == "4bcf45a1"


def test_an_exception_arrives_as_text():
    try:
        raise RuntimeError("the tool blew up")
    except RuntimeError:
        import sys

        record = a_record("call failed")
        record.exc_info = sys.exc_info()

    line = json.loads(JsonFormatter("master").format(record))

    assert "the tool blew up" in line["exception"]


def test_without_a_collector_tracing_stays_off(monkeypatch, caplog):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    with caplog.at_level(logging.INFO):
        assert configure_tracing("master") is False

    # A template that needed a collector to start is a template nobody runs.
    assert "Telemetry off" in caplog.text


@pytest.mark.asyncio
async def test_the_app_starts_with_tracing_off(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from master_agent.agents.master import build_master_agent
    from master_agent.chat_clients.fake import FakeStreamingChatClient
    from master_agent.server.app import create_app

    app = create_app(
        agent=build_master_agent(chat_client=FakeStreamingChatClient(chunks=["ok"]))
    )

    assert app is not None
