"""The step that is given a goal instead of a path, and the ceilings on it.

What is tested here is our half: that a definition without ceilings is refused,
that the budget is enforced by whoever spends it, and that what comes out is a
plain step output the history can hold. The planning loop itself is a model
deciding things -- running it here would be testing the model, so it is
verified against the real agents instead, and the README carries the numbers.
"""
from __future__ import annotations

from typing import Any

import pytest
from conftest import needs_postgres
from dbos import DBOS, SetWorkflowID

from process_service.catalog import Catalog
from process_service.definitions import DefinitionError, parse_definition
from process_service.engine import Engine, advance_instance, use_engine
from process_service.open_goal import STOPPED_BY_BUDGET, RemoteParticipant, Spent
from process_service.tools import tool

pytestmark = [needs_postgres, pytest.mark.integration]

OPEN = parse_definition(
    {
        "id": "open",
        "version": 1,
        "steps": [
            {
                "id": "work_it_out",
                "type": "open_goal",
                "participants": ["knowledge", "analysis"],
                "limits": {"max_rounds": 3, "max_agents": 2, "max_tokens": 20000},
                "input": {
                    "goal": "Decide whether the numbers agree with the documents."
                },
            },
            {
                "id": "write_it_down",
                "type": "tool",
                "tool": "keep_the_answer",
                "depends_on": ["work_it_out"],
            },
        ],
    }
)

KEPT: list[str] = []


@tool("keep_the_answer")
def keep_the_answer(context: dict[str, Any]) -> dict[str, Any]:
    KEPT.append(str(context.get("text", "")))
    return {"kept": True}


class FakeGateway:
    """Two agents that answer at once, each round costing what the test says."""

    def __init__(self, cost: int = 100) -> None:
        self.asked: list[tuple[str, str]] = []
        self.cost = cost

    def knows(self, name: str) -> bool:
        return True

    def known(self) -> list[str]:
        return ["knowledge", "analysis"]

    async def describe(self, name: str) -> str:
        return f"the {name} agent"

    async def converse(self, *, agent: str, question: str) -> dict[str, Any]:
        self.asked.append((agent, question))
        return {
            "text": f"{agent} says something about '{question[:20]}'",
            "usage": {"rounds": 1, "input_tokens": self.cost, "output_tokens": 0},
        }


def test_an_open_goal_without_ceilings_is_refused_at_startup():
    with pytest.raises(DefinitionError) as refused:
        parse_definition(
            {
                "id": "unbounded",
                "version": 1,
                "steps": [
                    {"id": "think", "type": "open_goal", "participants": ["knowledge"]}
                ],
            }
        )

    # A node that chooses its own path and has no ceiling is an open account.
    assert "max_rounds" in str(refused.value)
    assert "does not go" in str(refused.value)


def test_more_participants_than_the_ceiling_allows_is_refused():
    with pytest.raises(DefinitionError) as refused:
        parse_definition(
            {
                "id": "too-many",
                "version": 1,
                "steps": [
                    {
                        "id": "think",
                        "type": "open_goal",
                        "participants": ["a", "b", "c"],
                        "limits": {"max_rounds": 2, "max_agents": 2, "max_tokens": 100},
                    }
                ],
            }
        )

    assert "3 participants" in str(refused.value)


async def test_a_participant_stops_answering_once_the_budget_is_gone():
    """The budget is enforced where it is spent, not where it is declared.

    A round can go over: what it costs is known only after it has been paid for.
    What the budget guarantees is that there is no round **after** the one that
    crossed the line.
    """
    gateway = FakeGateway(cost=60)
    spent = Spent(max_tokens=100)
    participant = RemoteParticipant("knowledge", "reads documents", gateway, spent)

    first = await participant.run("what do the documents say?")
    second = await participant.run("and now?")
    third = await participant.run("and after that?")

    assert "knowledge says something" in first.text
    assert "knowledge says something" in second.text
    # The refusal is an answer, not an exception: the manager reads it, writes
    # it in its ledger and wraps up instead of crashing mid-plan.
    assert STOPPED_BY_BUDGET in third.text
    assert spent.rounds == 2
    assert spent.tokens == 120
    assert len(gateway.asked) == 2


async def test_the_result_of_an_open_goal_is_an_ordinary_step_output(
    store, scope, dbos
):
    KEPT.clear()
    reached = {
        "text": "they agree, with one exception",
        "stopped_by": "the manager finished",
        "rounds": 3,
        "input_tokens": 4200,
        "output_tokens": 380,
        "by_agent": {"knowledge": 2, "analysis": 1},
    }

    async def pursue(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["max_rounds"] == 3
        assert kwargs["max_tokens"] == 20000
        assert [name for name, _ in kwargs["participants"]] == ["knowledge", "analysis"]
        assert "numbers agree" in kwargs["goal"]
        return reached

    use_engine(Engine(Catalog([OPEN]), store, FakeGateway(), pursue=pursue))
    instance = await store.create(scope=scope, definition=OPEN, payload={})

    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    assert await handle.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    node = next(step for step in read.steps if step.step_id == "work_it_out")
    # Nothing of the conversation inside the node reaches the history: what is
    # written down is the answer and what it cost, which is what a replay needs.
    assert node.status == "completed"
    assert node.output == reached
    assert "the manager finished" in node.note
    # And the step after it read the answer out of the context, like any other.
    assert KEPT == ["they agree, with one exception"]


async def test_what_the_node_cost_is_in_the_history(store, scope, dbos):
    async def pursue(**kwargs: Any) -> dict[str, Any]:
        return {
            "text": "done",
            "stopped_by": "token budget",
            "rounds": 5,
            "input_tokens": 19000,
            "output_tokens": 1200,
            "by_agent": {"knowledge": 5},
        }

    use_engine(Engine(Catalog([OPEN]), store, FakeGateway(), pursue=pursue))
    instance = await store.create(scope=scope, definition=OPEN, payload={})

    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    assert await handle.get_result() == "completed"

    spent = [
        event
        for event in await store.events_of(instance_id=instance.id)
        if event.kind == "step_usage"
    ]
    assert spent[0].data["rounds"] == 5
    assert spent[0].data["input_tokens"] == 19000
    # Why it stopped is part of the cost: a node that ran out of budget and one
    # that finished its plan cost the same and mean different things.
    assert spent[0].data["stopped_by"] == "token budget"
