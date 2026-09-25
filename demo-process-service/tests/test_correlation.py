"""Finding an instance from a log line, and a trace from an instance.

A question crosses three services and leaves lines in all of them. What makes
those lines usable months later is that each one says which instance it belongs
to, and that the instance says which trace it ran in.
"""
from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from typing import Any

import pytest
from conftest import needs_postgres
from dbos import DBOS, SetWorkflowID
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import Engine, advance_instance, use_engine
from process_service.observability import JsonFormatter, current_trace, working_on
from process_service.tools import tool

pytestmark = [needs_postgres, pytest.mark.integration]

ONE_STEP = parse_definition(
    {
        "id": "one-step",
        "version": 1,
        "steps": [{"id": "only", "type": "tool", "tool": "say_something"}],
    }
)


@tool("say_something")
def say_something(context: dict[str, Any]) -> dict[str, Any]:
    logging.getLogger("test.tool").info("doing the work")
    return {"said": True}


@pytest.fixture
async def engine(store, dbos):
    running = Engine(Catalog([ONE_STEP]), store)
    use_engine(running)
    return running


@contextmanager
def json_logs():
    """Listens to the root logger the way a collector would."""
    written = io.StringIO()
    handler = logging.StreamHandler(written)
    handler.setFormatter(JsonFormatter("process-service"))
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield written
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


async def test_every_line_written_while_a_step_runs_names_the_instance(
    engine, store, scope
):
    instance = await store.create(scope=scope, definition=ONE_STEP, payload={})
    with json_logs() as written:
        with SetWorkflowID(str(instance.id)):
            handle = await DBOS.start_workflow_async(
                advance_instance, str(instance.id), scope
            )
        assert await handle.get_result() == "completed"

    lines = [
        json.loads(line) for line in written.getvalue().splitlines() if line.strip()
    ]
    from_the_tool = [line for line in lines if line["logger"] == "test.tool"]

    # The tool did not say which instance it was working for, and did not have
    # to: the line carries it because the step was running.
    assert from_the_tool
    assert all(line["instance_id"] == str(instance.id) for line in from_the_tool)


async def test_outside_a_step_a_line_claims_no_instance():
    with json_logs() as written:
        logging.getLogger("test.outside").info("nothing to do with an instance")
        with working_on("11111111-1111-1111-1111-111111111111"):
            logging.getLogger("test.inside").info("inside")

    lines = {
        json.loads(line)["logger"]: json.loads(line)
        for line in written.getvalue().splitlines()
        if line.strip()
    }
    assert "instance_id" not in lines["test.outside"]
    assert lines["test.inside"]["instance_id"] == "11111111-1111-1111-1111-111111111111"


async def test_an_instance_that_runs_inside_a_trace_writes_it_down(
    engine, store, scope
):
    """Without a collector there is no trace and nothing is written.

    With one, the instance keeps the trace it ran in: from a row somebody is
    looking at, the lines of all three services can be found.
    """
    trace.set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer("tests")
    instance = await store.create(scope=scope, definition=ONE_STEP, payload={})

    with tracer.start_as_current_span("a question") as span:
        expected = current_trace()
        with SetWorkflowID(str(instance.id)):
            handle = await DBOS.start_workflow_async(
                advance_instance, str(instance.id), scope
            )
        assert await handle.get_result() == "completed"

    assert expected == format(span.get_span_context().trace_id, "032x")
    events = await store.events_of(instance_id=instance.id)
    written = [event for event in events if event.kind == "trace"]
    assert [event.data["trace_id"] for event in written] == [expected]
