"""A tool with effects stops for a person, and runs only with their approval.

The whole path, as the interface drives it: the run ends in an AG-UI
interrupt naming the tool call; the next run carries a `resume` entry with the
person's answer; the tool runs if, and only if, the answer was yes.
"""

from __future__ import annotations

import json

import httpx
import pytest

from master_agent.agents.master import build_master_agent
from master_agent.chat_clients.fake import ToolCallingFakeClient
from master_agent.server.app import create_app


@pytest.fixture
def process_service(monkeypatch):
    """A fake process service that records every start."""
    started: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/instances"):
            started.append(json.loads(request.content))
            return httpx.Response(
                201,
                json={
                    "id": "0f4c2d6e-1111-4222-8333-944455556666",
                    "process_id": "example-approval",
                    "process_version": 1,
                    "status": "running",
                },
            )
        return httpx.Response(404, json={"detail": "no route"})

    original = httpx.AsyncClient

    class Patched(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            if "transport" not in kwargs:
                kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")
    monkeypatch.setenv("MASTER_PROCESS_SERVICE_URL", "http://process")
    monkeypatch.setattr("master_agent.tools.process_tools.httpx.AsyncClient", Patched)
    return started


def the_app():
    client = ToolCallingFakeClient(
        tool_name="start_process",
        tool_args={"process_id": "example-approval", "input_json": '{"amount": 5}'},
        final_text="Started.",
    )
    return create_app(agent=build_master_agent(chat_client=client))


async def run(app, body: dict) -> list[dict]:
    transport = httpx.ASGITransport(app=app)
    events: list[dict] = []
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        http.stream(
            "POST", "/agui", json=body, headers={"Accept": "text/event-stream"}
        ) as response,
    ):
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events


def first_turn(thread: str) -> dict:
    return {
        "threadId": thread,
        "runId": "run-1",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": "start the approval"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def answer(thread: str, interrupt_id: str, approved: bool) -> dict:
    return {
        "threadId": thread,
        "runId": "run-2",
        "state": {},
        "messages": [],
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "resume": [
            {
                "interruptId": interrupt_id,
                "status": "resolved",
                "payload": {"approved": approved},
            }
        ],
    }


def interrupt_of(events: list[dict]) -> dict:
    [finished] = [e for e in events if e["type"] == "RUN_FINISHED"]
    outcome = finished.get("outcome") or {}
    assert outcome.get("type") == "interrupt", finished
    [interrupt] = outcome["interrupts"]
    return interrupt


@pytest.mark.asyncio
async def test_the_run_stops_before_the_tool_and_names_it(process_service):
    app = the_app()

    events = await run(app, first_turn("t-approve-1"))

    interrupt = interrupt_of(events)
    assert interrupt["reason"] == "tool_call"
    assert "start_process" in json.dumps(interrupt)
    # Nothing ran: the process service never heard of it.
    assert process_service == []


@pytest.mark.asyncio
async def test_a_yes_runs_the_tool(process_service):
    app = the_app()
    interrupt = interrupt_of(await run(app, first_turn("t-approve-2")))

    await run(app, answer("t-approve-2", interrupt["id"], approved=True))

    assert len(process_service) == 1
    assert process_service[0]["input"] == {"amount": 5}


@pytest.mark.asyncio
async def test_a_no_does_not(process_service):
    app = the_app()
    interrupt = interrupt_of(await run(app, first_turn("t-approve-3")))

    await run(app, answer("t-approve-3", interrupt["id"], approved=False))

    assert process_service == []


def error_of(events: list[dict]) -> dict:
    [error] = [e for e in events if e["type"] == "RUN_ERROR"]
    return error


@pytest.mark.asyncio
async def test_the_same_yes_twice_runs_the_tool_once(process_service):
    """A retried request -- a reconnect, a double click -- replays the result."""
    app = the_app()
    interrupt = interrupt_of(await run(app, first_turn("t-approve-4")))

    await run(app, answer("t-approve-4", interrupt["id"], approved=True))
    replay = await run(app, answer("t-approve-4", interrupt["id"], approved=True))

    assert len(process_service) == 1
    assert [e["type"] for e in replay].count("TOOL_CALL_RESULT") == 1


@pytest.mark.asyncio
async def test_a_yes_reaching_another_replica_runs_nothing(process_service):
    """The approval lives in the process that asked for it (MAF keeps it in
    memory). A resume that lands on another replica must fail closed, with an
    error the interface can explain -- never run the tool unasked."""
    asking, other = the_app(), the_app()
    interrupt = interrupt_of(await run(asking, first_turn("t-approve-5")))

    events = await run(other, answer("t-approve-5", interrupt["id"], approved=True))

    assert error_of(events)["code"] == "APPROVAL_RESUME_NOT_FOUND"
    assert process_service == []


@pytest.mark.asyncio
async def test_a_pending_approval_must_be_answered_before_anything_else(
    process_service,
):
    """AG-UI: while a thread has open interrupts, every input must resolve
    them. A plain new message is refused, and the question stays open."""
    app = the_app()
    interrupt = interrupt_of(await run(app, first_turn("t-approve-6")))
    unrelated = first_turn("t-approve-6") | {
        "runId": "run-2",
        "messages": [{"id": "m2", "role": "user", "content": "something else"}],
    }

    refused = await run(app, unrelated)
    await run(app, answer("t-approve-6", interrupt["id"], approved=True))

    assert error_of(refused)["code"] == "APPROVAL_RESUME_REQUIRED"
    assert len(process_service) == 1


@pytest.mark.asyncio
async def test_a_cancelled_approval_cannot_be_approved_later(process_service):
    """What the voice channel does with a question it cannot ask."""
    app = the_app()
    interrupt = interrupt_of(await run(app, first_turn("t-approve-7")))
    cancel = answer("t-approve-7", interrupt["id"], approved=True)
    cancel["resume"] = [{"interruptId": interrupt["id"], "status": "cancelled"}]

    await run(app, cancel)
    late = await run(app, answer("t-approve-7", interrupt["id"], approved=True))

    assert error_of(late)["code"] == "APPROVAL_RESUME_INVALID"
    assert process_service == []
