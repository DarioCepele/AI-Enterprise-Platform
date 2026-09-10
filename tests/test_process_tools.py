"""The seam between a conversation and a process that outlives it."""
from __future__ import annotations

import json

import httpx
import pytest

from demo.tools.process_tools import build_process_tools

SCOPE = "test-scope"

INSTANCE = {
    "id": "006c3ee6-9816-4f13-a169-718cb107864b",
    "scope": SCOPE,
    "process_id": "example-approval",
    "process_version": 1,
    "status": "waiting_approval",
    "note": None,
    "input": {"amount": 25000},
    "context": {},
    "created_at": "2026-09-09T20:58:28Z",
    "updated_at": "2026-09-09T20:59:43Z",
    "steps": [
        {
            "step_id": "collect",
            "status": "completed",
            "owner": None,
            "task_id": None,
            "question": None,
            "output": {},
            "note": None,
            "started_at": None,
            "ended_at": None,
        },
        {
            "step_id": "approval",
            "status": "waiting_approval",
            "owner": None,
            "task_id": None,
            "question": "waiting for a decision by reviewer",
            "output": None,
            "note": None,
            "started_at": None,
            "ended_at": None,
        },
    ],
}


@pytest.fixture
def service(monkeypatch):
    """Records what was asked, and answers what the test says."""
    seen: list[httpx.Request] = []
    replies: dict[str, tuple[int, dict]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for path, (status, body) in replies.items():
            if path in str(request.url):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": f"no route for {request.url.path}"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class Patched(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    return {"seen": seen, "replies": replies}


def tools():
    return {tool.name: tool for tool in build_process_tools("http://processes.test", SCOPE)}


def test_without_a_process_service_there_are_no_tools():
    # The model must not be shown a tool that cannot work: it would call it.
    assert build_process_tools("", SCOPE) == []


@pytest.mark.asyncio
async def test_starting_a_process_says_it_goes_on_without_the_conversation(service):
    service["replies"]["/instances"] = (201, INSTANCE)

    answer = await tools()["start_process"].func(
        process_id="example-approval", input_json='{"amount": 25000}'
    )

    assert INSTANCE["id"] in answer.text
    # The point the agent has to pass on: this thing outlives the browser.
    assert "goes on by itself" in answer.text
    assert json.loads(service["seen"][-1].content) == {"input": {"amount": 25000}}


@pytest.mark.asyncio
async def test_the_started_instance_goes_into_the_shared_state(service):
    service["replies"]["/instances"] = (201, INSTANCE)

    answer = await tools()["start_process"].func(process_id="example-approval")

    carried = answer.additional_properties["__ag_ui_tool_result_state__"]
    assert carried["process_instance"]["id"] == INSTANCE["id"]


@pytest.mark.asyncio
async def test_the_scope_travels_with_every_call(service):
    service["replies"]["/processes"] = (200, {"processes": []})

    await tools()["list_processes"].func()

    assert service["seen"][-1].headers["X-Process-Scope"] == SCOPE


@pytest.mark.asyncio
async def test_an_input_that_is_not_json_is_refused_before_the_call(service):
    answer = await tools()["start_process"].func(process_id="p", input_json="{not json")

    assert "not valid JSON" in answer.text
    assert service["seen"] == []


@pytest.mark.asyncio
async def test_the_status_says_what_is_waiting_and_who_has_to_act(service):
    service["replies"]["/instances/"] = (200, INSTANCE)

    answer = await tools()["process_status"].func(instance_id=INSTANCE["id"])

    assert "waiting for a decision from a person" in answer.text
    assert "waiting for a decision by reviewer" in answer.text
    # The agent must not decide in the user's place, and must not think it can.
    assert "up to a person" in answer.text
    assert "1 of 2 steps completed" in answer.text


@pytest.mark.asyncio
async def test_what_the_service_refused_is_passed_on_in_its_own_words(service):
    service["replies"]["/processes/nothing/instances"] = (
        404,
        {"detail": "process 'nothing' is not in the catalogue. Known: example-approval"},
    )

    answer = await tools()["start_process"].func(process_id="nothing")

    # "404" would send the model to guess; the catalogue is in the message.
    assert "not in the catalogue" in answer.text
    assert "example-approval" in answer.text


@pytest.mark.asyncio
async def test_a_service_that_does_not_answer_does_not_break_the_turn(service, monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    class Patched(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(refuse)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)

    answer = await tools()["process_status"].func(instance_id="whatever")

    assert "could not read the instance" in answer.text
