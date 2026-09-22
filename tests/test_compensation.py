"""Taking back what was already done, when the process is not going to finish.

A process that stops halfway has usually already touched something outside
itself. Compensation is the part that says what happens to that, and it is the
part nobody writes until the first time it is needed.
"""
from __future__ import annotations

from typing import Any

import pytest
from conftest import needs_postgres
from dbos import DBOS, SetWorkflowID

from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import (
    Engine,
    advance_instance,
    approval_topic,
    step_workflow_id,
    use_engine,
)
from process_service.tools import tool

pytestmark = [needs_postgres, pytest.mark.integration]

BREAKS_AT_THE_END = parse_definition(
    {
        "id": "breaks-at-the-end",
        "version": 1,
        "steps": [
            {
                "id": "book",
                "type": "tool",
                "tool": "book_it",
                "idempotency_key": "request_id",
                "compensate_with": "unbook_it",
            },
            {
                "id": "charge",
                "type": "tool",
                "tool": "charge_it",
                "depends_on": ["book"],
                "compensate_with": "refund_it",
            },
            {
                "id": "deliver",
                "type": "tool",
                "tool": "cannot_do_it",
                "depends_on": ["charge"],
            },
        ],
    }
)

UNDO_BREAKS_TOO = parse_definition(
    {
        "id": "undo-breaks-too",
        "version": 1,
        "steps": [
            {
                "id": "book",
                "type": "tool",
                "tool": "book_it",
                "compensate_with": "unbook_it",
            },
            {
                "id": "charge",
                "type": "tool",
                "tool": "charge_it",
                "depends_on": ["book"],
                "compensate_with": "cannot_undo_it",
            },
            {
                "id": "deliver",
                "type": "tool",
                "tool": "cannot_do_it",
                "depends_on": ["charge"],
            },
        ],
    }
)

REFUSED = parse_definition(
    {
        "id": "refused",
        "version": 1,
        "steps": [
            {
                "id": "book",
                "type": "tool",
                "tool": "book_it",
                "compensate_with": "unbook_it",
            },
            {
                "id": "sign_off",
                "type": "approval",
                "approvers": ["reviewer"],
                "depends_on": ["book"],
            },
        ],
    }
)

CALLS: list[str] = []


@tool("book_it")
def book_it(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("book")
    return {"booked": True}


@tool("charge_it")
def charge_it(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("charge")
    return {"charged": True}


@tool("cannot_do_it")
def cannot_do_it(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("break")
    raise RuntimeError("this does not work")


@tool("cannot_undo_it")
def cannot_undo_it(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("break")
    raise RuntimeError("this does not work either")


@tool("unbook_it")
def unbook_it(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("unbook")
    return {"unbooked": True}


@tool("refund_it")
def refund_it(context: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("refund")
    return {"refunded": True}


@pytest.fixture
async def engine(store, dbos):
    CALLS.clear()
    running = Engine(Catalog([BREAKS_AT_THE_END, UNDO_BREAKS_TOO, REFUSED]), store)
    use_engine(running)
    return running


def step_of(instance, step_id: str):
    return next(step for step in instance.steps if step.step_id == step_id)


async def run(store, scope, definition, payload=None):
    instance = await store.create(
        scope=scope, definition=definition, payload=payload or {}
    )
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    return instance, handle


async def test_a_step_that_fails_undoes_the_ones_before_it_last_first(
    engine, store, scope
):
    instance, handle = await run(store, scope, BREAKS_AT_THE_END, {"request_id": "r-1"})

    assert await handle.get_result() == "compensated"

    # Reverse order matters: the charge is taken back before the booking it was
    # made against.
    assert CALLS == ["book", "charge", "break", "refund", "unbook"]

    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "deliver").status == "failed"
    assert step_of(read, "charge").status == "compensated"
    assert step_of(read, "book").status == "compensated"
    # What a compensated step did is still part of the history.
    assert step_of(read, "charge").output == {"charged": True}
    assert "refund_it" in step_of(read, "charge").note


async def test_a_compensated_instance_says_so_instead_of_just_failing(
    engine, store, scope
):
    instance, handle = await run(store, scope, BREAKS_AT_THE_END, {"request_id": "r-2"})

    assert await handle.get_result() == "compensated"

    read = await store.get(scope=scope, instance_id=instance.id)
    # 'failed' would leave open whether anything was taken back. The state says
    # that it was, and the note says what.
    assert read.status == "compensated"
    assert "failed: deliver" in read.note
    assert "undone: charge, book" in read.note


async def test_a_compensation_that_fails_does_not_stop_the_others(engine, store, scope):
    instance, handle = await run(store, scope, UNDO_BREAKS_TOO)

    assert await handle.get_result() == "compensated"

    read = await store.get(scope=scope, instance_id=instance.id)
    # The refund could not be taken back; the booking still was.
    assert step_of(read, "charge").status == "compensation_failed"
    assert "does not work either" in step_of(read, "charge").note
    assert step_of(read, "book").status == "compensated"
    assert CALLS == ["book", "charge", "break", "break", "unbook"]
    assert "undone: book" in read.note


async def test_a_refusal_also_takes_back_what_was_already_done(engine, store, scope):
    instance, handle = await run(store, scope, REFUSED)

    for _ in range(200):
        read = await store.get(scope=scope, instance_id=instance.id)
        if read.status == "waiting_approval":
            break
    await DBOS.send_async(
        destination_id=step_workflow_id(str(instance.id), "sign_off"),
        message={"decision": "rejected", "by": "reviewer", "note": "no"},
        topic=approval_topic("sign_off"),
    )

    # A no stops the process as surely as a failure does, and what was booked
    # before the question was asked has to go back.
    assert await handle.get_result() == "rejected"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "book").status == "compensated"
    assert read.status == "rejected"
    assert "undone: book" in read.note
    assert CALLS == ["book", "unbook"]


async def test_undoing_twice_undoes_once(engine, store, scope):
    """The compensation of an idempotent step carries a key of its own.

    Running the instance again -- a retry, a recovery, somebody pressing the
    button twice -- must not send a second refund.
    """
    instance, handle = await run(store, scope, BREAKS_AT_THE_END, {"request_id": "r-3"})
    assert await handle.get_result() == "compensated"
    CALLS.clear()

    # The application version has to be said out loud: a fork inherits the
    # version of the workflow it comes from, and only an executor running that
    # version will pick it up -- after a deploy, that is nobody.
    again = await DBOS.fork_workflow_async(
        str(instance.id), start_step=1, application_version=DBOS.application_version
    )
    assert await again.get_result() == "compensated"

    # 'book' declared an idempotency key, so its undo is recorded under one too
    # and does not run a second time. 'charge' declared none, and its undo runs
    # again -- which is what the key is for, and why the effect that matters
    # should declare one.
    assert "unbook" not in CALLS
    assert "refund" in CALLS
