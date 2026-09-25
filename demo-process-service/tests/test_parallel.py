"""Steps that do not depend on each other, and the join that waits for them.

A process where everything is a queue of one is a process that takes as long as
the sum of its parts. What has to hold: independent steps really overlap, and
when they stop the instance says which ones did not get through.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from conftest import needs_postgres
from dbos import DBOS, SetWorkflowID

from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import (
    Engine,
    advance_instance,
    step_workflow_id,
    use_engine,
)
from process_service.tools import tool

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
            {
                "id": "one",
                "type": "agent",
                "owner": "knowledge",
                "input": {"question": "first?"},
            },
            {
                "id": "two",
                "type": "agent",
                "owner": "knowledge",
                "input": {"question": "second?"},
            },
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

    async def ask(
        self, *, agent: str, question: str, scope: str, instance_id: str, step_id: str
    ):
        self.asked.append(step_id)
        return {"task_id": f"task-{step_id}", "state": "TASK_STATE_SUBMITTED"}

    async def result_of(self, *, agent: str, task_id: str):
        return {"text": f"answer of {task_id}", "usage": {}}


@pytest.fixture
def agents() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
async def engine(store, agents, dbos):
    CALLS.clear()
    ARRIVED.clear()
    BOTH_IN.clear()
    running = Engine(
        Catalog([TWO_AT_ONCE, ONE_OF_THEM_BREAKS, TWO_AGENTS]), store, agents
    )
    use_engine(running)
    return running


def step_of(instance, step_id: str):
    return next(step for step in instance.steps if step.step_id == step_id)


async def start(store, scope, definition):
    instance = await store.create(scope=scope, definition=definition, payload={})
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
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


class TimedAgent(FakeAgent):
    """Records when each task was started, so the answers can be timed from it."""

    def __init__(self) -> None:
        super().__init__()
        self.asked_at: dict[str, float] = {}

    async def ask(
        self, *, agent: str, question: str, scope: str, instance_id: str, step_id: str
    ):
        self.asked_at[step_id] = time.perf_counter()
        return await super().ask(
            agent=agent,
            question=question,
            scope=scope,
            instance_id=instance_id,
            step_id=step_id,
        )


ANSWER_AFTER = 0.6


async def answer_each_step(agents: TimedAgent, instance_id: str, expected: int) -> None:
    """Answers every task exactly `ANSWER_AFTER` seconds after it was started.

    This is the shape of a slow agent, without a slow agent: if the two steps
    are started together the two answers arrive together, and if they were
    started one after the other the second clock only starts when the first is
    over.
    """
    answered: set[str] = set()
    while len(answered) < expected:
        for step_id, began in list(agents.asked_at.items()):
            if step_id in answered or time.perf_counter() - began < ANSWER_AFTER:
                continue
            answered.add(step_id)
            await DBOS.send_async(
                destination_id=step_workflow_id(instance_id, step_id),
                message={
                    "task_id": f"task-{step_id}",
                    "state": "TASK_STATE_COMPLETED",
                    "text": step_id,
                },
                topic=step_id,
            )
        await asyncio.sleep(0.02)


async def test_two_agents_that_take_a_while_cost_the_slower_one_not_the_sum(
    store, scope, dbos
):
    CALLS.clear()
    agents = TimedAgent()
    use_engine(Engine(Catalog([TWO_AGENTS]), store, agents))
    instance = await store.create(scope=scope, definition=TWO_AGENTS, payload={})

    began = time.perf_counter()
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    result, _ = await asyncio.gather(
        handle.get_result(), answer_each_step(agents, str(instance.id), expected=2)
    )
    took = time.perf_counter() - began

    assert result == "completed"
    assert sorted(agents.asked) == ["one", "two"]
    # Two waits of 0.6s each: together they cost about 0.6s, one after the other
    # they would cost 1.2s. The margin is wide enough not to depend on the
    # machine the test runs on.
    assert took < 2 * ANSWER_AFTER


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
            message={
                "task_id": f"task-{name}",
                "state": "TASK_STATE_COMPLETED",
                "text": name,
            },
            topic=name,
        )

    assert await handle.get_result() == "completed"
    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "join").status == "completed"
