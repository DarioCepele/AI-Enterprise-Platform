"""Steps that do not depend on each other, and the join that waits for them.

A process where everything is a queue of one is a process that takes as long as
the sum of its parts. What has to hold: independent steps really overlap, and
when they stop the instance says which ones did not get through.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dbos import DBOS, SetWorkflowID

from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import Engine, advance_instance, step_workflow_id, use_engine
from process_service.tools import tool

from conftest import needs_postgres

pytestmark = [needs_postgres, pytest.mark.integration]

TWO_AT_ONCE = parse_definition(
    {
        "id": "two-at-once",
        "version": 1,
        "steps": [
            {"id": "left", "type": "tool", "tool": "wait_for_the_other"},
            {"id": "right", "type": "tool", "tool": "wait_for_the_other"},
            {
                "id": "join",
                "type": "tool",
                "tool": "join_them",
                "depends_on": ["left", "right"],
            },
        ],
    }
)

ONE_OF_THEM_BREAKS = parse_definition(
    {
        "id": "one-of-them-breaks",
        "version": 1,
        "steps": [
            {"id": "sound", "type": "tool", "tool": "take_a_moment"},
            {"id": "broken", "type": "tool", "tool": "break_it"},
            {
                "id": "join",
                "type": "tool",
                "tool": "join_them",
                "depends_on": ["sound", "broken"],
            },
        ],
    }
)

TWO_AGENTS = parse_definition(
    {
        "id": "two-agents",
        "version": 1,
        "steps": [
            {"id": "one", "type": "agent", "owner": "knowledge", "input": {"question": "first?"}},
            {"id": "two", "type": "agent", "owner": "knowledge", "input": {"question": "second?"}},
            {
                "id": "join",
                "type": "tool",
                "tool": "join_them",
                "depends_on": ["one", "two"],
            },
        ],
    }
)

CALLS: list[str] = []
ARRIVED = asyncio.Event()
BOTH_IN = asyncio.Event()


@tool("wait_for_the_other")
async def wait_for_the_other(context: dict[str, Any]) -> dict[str, Any]:
    """Finishes only if the other one is running at the same time.

    Two steps that run one after the other would each wait here for a partner
    that has already finished, or has not started: the timeout is the failure of
    the claim, not flakiness.
    """
    if ARRIVED.is_set():
        BOTH_IN.set()
    else:
        ARRIVED.set()
    await asyncio.wait_for(BOTH_IN.wait(), timeout=10)
    CALLS.append("side")
    return {}


@tool("take_a_moment")
async def take_a_moment(context: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.3)
    CALLS.append("sound")
    return {"sound": True}


@tool("break_it")
def break_it(context: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("this step does not work")


@tool("join_them")
def join_them(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("join")
    return {"joined": True}


class FakeAgent:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def knows(self, name: str) -> bool:
        return True

    def known(self) -> list[str]:
        return ["knowledge"]

    async def ask(self, *, agent: str, question: str, scope: str, instance_id: str, step_id: str):
        self.asked.append(step_id)
        return {"task_id": f"task-{step_id}", "state": "TASK_STATE_SUBMITTED"}


@pytest.fixture
def agents() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
async def engine(store, agents, dbos):
    CALLS.clear()
    ARRIVED.clear()
    BOTH_IN.clear()
    running = Engine(Catalog([TWO_AT_ONCE, ONE_OF_THEM_BREAKS, TWO_AGENTS]), store, agents)
    use_engine(running)
    return running


def step_of(instance, step_id: str):
    return next(step for step in instance.steps if step.step_id == step_id)


async def start(store, scope, definition):
    instance = await store.create(scope=scope, definition=definition, payload={})
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(advance_instance, str(instance.id), scope)
    return instance, handle


async def test_two_steps_that_do_not_depend_on_each_other_run_together(
    engine, store, scope
):
    instance, handle = await start(store, scope, TWO_AT_ONCE)

    # Each side waits for the other: this finishes only because they overlap.
    assert await handle.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert CALLS == ["side", "side", "join"]
    assert step_of(read, "join").status == "completed"


async def test_the_join_waits_for_everyone_and_names_who_failed(engine, store, scope):
    instance, handle = await start(store, scope, ONE_OF_THEM_BREAKS)

    assert await handle.get_result() == "failed"

    read = await store.get(scope=scope, instance_id=instance.id)
    # The step that worked was not abandoned halfway because its neighbour
    # broke: the join waited for it, and the instance says which one failed.
    assert CALLS == ["sound"]
    assert step_of(read, "sound").status == "completed"
    assert step_of(read, "broken").status == "failed"
    assert "does not work" in step_of(read, "broken").note
    assert read.status == "failed"
    assert read.note == "failed: broken"
    assert step_of(read, "join").status == "pending"


async def test_two_agents_can_be_waiting_at_the_same_time(engine, store, scope, agents):
    instance, handle = await start(store, scope, TWO_AGENTS)

    for _ in range(200):
        read = await store.get(scope=scope, instance_id=instance.id)
        if all(step_of(read, name).status == "waiting" for name in ("one", "two")):
            break
        await asyncio.sleep(0.05)

    # Two remote tasks in flight for the same instance: neither is holding the
    # other, and each waits on the workflow of its own step.
    assert sorted(agents.asked) == ["one", "two"]
    assert step_of(read, "one").task_id == "task-one"
    assert step_of(read, "two").task_id == "task-two"

    for name in ("one", "two"):
        await DBOS.send_async(
            destination_id=step_workflow_id(str(instance.id), name),
            message={"task_id": f"task-{name}", "state": "TASK_STATE_COMPLETED", "text": name},
            topic=name,
        )

    assert await handle.get_result() == "completed"
    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "join").status == "completed"
