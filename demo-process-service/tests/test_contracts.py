"""What this service puts on the wire, checked against the shared contract."""
from __future__ import annotations

import pytest
from conftest import needs_postgres
from contracts import assert_shape, load

from process_service.definitions import parse_definition

pytestmark = [needs_postgres, pytest.mark.integration]

WAITS = parse_definition(
    {
        "id": "waits",
        "version": 1,
        "steps": [
            {"id": "approval", "type": "approval", "approvers": ["reviewer"]},
        ],
    }
)


async def test_an_instance_matches_the_contract(store, scope):
    """The shape an interface reads, taken from a real row and not from a fixture."""
    instance = await store.create(
        scope=scope, definition=WAITS, payload={"amount": 25000, "request_id": "r-1"}
    )
    await store.waiting_on(
        instance_id=instance.id,
        step_id="approval",
        status="waiting_approval",
        task_id="40b88cef-6a1f-4c2e-9a55-1b0d4f2c77aa",
        question="waiting for a decision by reviewer",
    )
    await store.finish_step(
        instance_id=instance.id,
        step_id="approval",
        status="waiting_approval",
        output={"decision": "approved", "by": "reviewer", "note": None},
        note="no answer within 120s, escalated to 'by_hand'",
    )
    await store.set_status(
        instance_id=instance.id,
        status="waiting_approval",
        note="failed: notify; undone: apply",
    )

    read = await store.get(scope=scope, instance_id=instance.id)

    assert_shape("process/instance", read.model_dump(mode="json"))


def test_the_contract_names_this_repository_as_the_producer():
    # If this ever fails, the contract moved and the failure message above would
    # send whoever broke it to the wrong repository.
    assert "demo-process-service" in load("process/instance")["produced_by"]
