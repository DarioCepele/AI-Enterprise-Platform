"""The history of an instance, and walking it again months later.

The question these answer is "why did it go that way", asked by somebody who has
the rows and nothing else: no logs, no model, no agent still running.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from conftest import needs_postgres
from dbos import DBOS, SetWorkflowID

from process_service.api import create_app
from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import (
    Engine,
    advance_instance,
    step_workflow_id,
    use_engine,
)
from process_service.replay import ReplayDiverged, replay
from process_service.tools import tool

pytestmark = [needs_postgres, pytest.mark.integration]

BRANCHES = parse_definition(
    {
        "id": "branches",
        "version": 1,
        "steps": [
            {"id": "read_it", "type": "tool", "tool": "read_the_request"},
            {
                "id": "decide",
                "type": "decision",
                "depends_on": ["read_it"],
                "branches": [
                    {"when": "amount > 10000", "goto": "big"},
                    {"when": "true", "goto": "small"},
                ],
            },
            {"id": "big", "type": "tool", "tool": "handle_big"},
            {"id": "small", "type": "tool", "tool": "handle_small"},
        ],
    }
)

WITH_AN_AGENT = parse_definition(
    {
        "id": "with-an-agent",
        "version": 1,
        "steps": [
            {
                "id": "ask",
                "type": "agent",
                "owner": "knowledge",
                "input": {"question": "what is the rule?"},
            },
            {
                "id": "close",
                "type": "tool",
                "tool": "handle_small",
                "depends_on": ["ask"],
            },
        ],
    }
)

CALLS: list[str] = []


@tool("read_the_request")
def read_the_request(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("read_it")
    return {"amount": context.get("amount")}


@tool("handle_big")
def handle_big(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("big")
    return {"handled": "big"}


@tool("handle_small")
def handle_small(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("small")
    return {"handled": "small"}


class FakeAgent:
    def knows(self, name: str) -> bool:
        return True

    def known(self) -> list[str]:
        return ["knowledge"]

    async def ask(
        self, *, agent: str, question: str, scope: str, instance_id: str, step_id: str
    ):
        CALLS.append("asked")
        return {"task_id": "task-1", "state": "TASK_STATE_SUBMITTED"}


@pytest.fixture
async def engine(store, dbos):
    CALLS.clear()
    running = Engine(Catalog([BRANCHES, WITH_AN_AGENT]), store, FakeAgent())
    use_engine(running)
    return running


@pytest.fixture
def app(store):
    return create_app(catalog=Catalog([BRANCHES, WITH_AN_AGENT]), store=store)


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def run(store, scope, definition, payload=None):
    instance = await store.create(
        scope=scope, definition=definition, payload=payload or {}
    )
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    return instance, handle


async def test_the_history_says_what_happened_and_in_which_order(engine, store, scope):
    instance, handle = await run(store, scope, BRANCHES, {"amount": 25000})
    assert await handle.get_result() == "completed"

    events = await store.events_of(instance_id=instance.id)
    kinds = [(event.kind, event.step_id) for event in events]

    assert ("instance_created", None) in kinds
    assert kinds.index(("step_finished", "read_it")) < kinds.index(
        ("step_finished", "decide")
    )
    # A decision is only useful in the history if it says on which condition.
    decided = next(
        event
        for event in events
        if event.kind == "step_finished" and event.step_id == "decide"
    )
    assert decided.data["output"] == {"when": "amount > 10000", "goto": "big"}
    assert kinds[-1] == ("instance_status", None)


async def test_replaying_a_finished_instance_takes_the_same_path(engine, store, scope):
    instance, handle = await run(store, scope, BRANCHES, {"amount": 25000})
    assert await handle.get_result() == "completed"
    CALLS.clear()

    walked = replay(BRANCHES, await store.events_of(instance_id=instance.id))

    assert walked.path == ["read_it", "decide", "big"]
    assert walked.decisions == {"decide": "big"}
    assert walked.status == "completed"
    # Nothing ran: the replay is made of the recorded events and the rules.
    assert CALLS == []


async def test_the_other_branch_replays_the_other_way(engine, store, scope):
    instance, handle = await run(store, scope, BRANCHES, {"amount": 10})
    assert await handle.get_result() == "completed"

    walked = replay(BRANCHES, await store.events_of(instance_id=instance.id))

    assert walked.path == ["read_it", "decide", "small"]


async def test_two_replays_of_the_same_history_do_not_diverge(engine, store, scope):
    """What the agent said is data in the history, not something asked again."""
    instance, handle = await run(store, scope, WITH_AN_AGENT)
    for _ in range(200):
        read = await store.get(scope=scope, instance_id=instance.id)
        if next(s for s in read.steps if s.step_id == "ask").status == "waiting":
            break
        await asyncio.sleep(0.05)
    await DBOS.send_async(
        destination_id=step_workflow_id(str(instance.id), "ask"),
        message={
            "task_id": "task-1",
            "state": "TASK_STATE_COMPLETED",
            "text": "the rule is this",
        },
        topic="ask",
    )
    assert await handle.get_result() == "completed"

    events = await store.events_of(instance_id=instance.id)
    first = replay(WITH_AN_AGENT, events)
    second = replay(WITH_AN_AGENT, events)

    assert first.path == second.path == ["ask", "close"]
    assert first.outputs["ask"]["text"] == "the rule is this"
    assert first.outputs == second.outputs


async def test_a_definition_that_changed_makes_the_history_stop_explaining(
    engine, store, scope
):
    instance, handle = await run(store, scope, BRANCHES, {"amount": 25000})
    assert await handle.get_result() == "completed"

    moved_threshold = parse_definition(
        {
            "id": "branches",
            "version": 2,
            "steps": [
                {"id": "read_it", "type": "tool", "tool": "read_the_request"},
                {
                    "id": "decide",
                    "type": "decision",
                    "depends_on": ["read_it"],
                    "branches": [
                        {"when": "amount > 100000", "goto": "big"},
                        {"when": "true", "goto": "small"},
                    ],
                },
                {"id": "big", "type": "tool", "tool": "handle_big"},
                {"id": "small", "type": "tool", "tool": "handle_small"},
            ],
        }
    )

    with pytest.raises(ReplayDiverged) as divergence:
        replay(moved_threshold, await store.events_of(instance_id=instance.id))

    # Silently replaying the new rule would be worse than refusing: the instance
    # went the other way, and the answer has to say so.
    assert "took 'big'" in str(divergence.value)
    assert "small" in str(divergence.value)


async def test_the_api_serves_the_history_and_the_replay(engine, app, store, scope):
    instance, handle = await run(store, scope, BRANCHES, {"amount": 25000})
    assert await handle.get_result() == "completed"
    headers = {"X-Process-Scope": scope}

    async with await client_for(app) as client:
        history = await client.get(f"/instances/{instance.id}/events", headers=headers)
        after = await client.get(
            f"/instances/{instance.id}/events",
            params={"after": history.json()["events"][0]["id"]},
            headers=headers,
        )
        walked = await client.get(f"/instances/{instance.id}/replay", headers=headers)

    assert history.json()["events"][0]["kind"] == "instance_created"
    assert len(after.json()["events"]) == len(history.json()["events"]) - 1
    assert walked.json()["path"] == ["read_it", "decide", "big"]
    assert walked.json()["decisions"] == {"decide": "big"}
