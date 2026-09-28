"""A step that waits for a person: what it costs, who decided, and what a no does.

An approval is the longest wait a process has, and the one that has to survive
everything: the restart, the day in between, the second click on the same
button.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from typing import Any

import httpx
import pytest
from conftest import POSTGRES_DSN, needs_postgres
from dbos import DBOS, SetWorkflowID

from process_service.api import create_app
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

NEEDS_A_YES = parse_definition(
    {
        "id": "needs-a-yes",
        "version": 1,
        "steps": [
            {"id": "prepare", "type": "tool", "tool": "prepare_it"},
            {
                "id": "sign_off",
                "type": "approval",
                "approvers": ["reviewer"],
                "depends_on": ["prepare"],
            },
            {
                "id": "apply",
                "type": "tool",
                "tool": "apply_it",
                "depends_on": ["sign_off"],
            },
        ],
    }
)

NOBODY_COMES = parse_definition(
    {
        "id": "nobody-comes",
        "version": 1,
        "steps": [
            {
                "id": "sign_off",
                "type": "approval",
                "approvers": ["reviewer"],
                "timeout_seconds": 1,
                "on_timeout": "by_the_manager",
            },
            {"id": "by_the_manager", "type": "tool", "tool": "escalate_it"},
        ],
    }
)

NOBODY_COMES_AND_NOWHERE_TO_GO = parse_definition(
    {
        "id": "nowhere-to-go",
        "version": 1,
        "steps": [
            {
                "id": "sign_off",
                "type": "approval",
                "approvers": ["reviewer"],
                "timeout_seconds": 1,
            }
        ],
    }
)

AFTER_A_BRANCH = parse_definition(
    {
        "id": "after-a-branch",
        "version": 1,
        "steps": [
            {
                "id": "decide",
                "type": "decision",
                "branches": [
                    {"when": "amount > 100", "goto": "sign_off"},
                    {"when": "true", "goto": "apply"},
                ],
            },
            {
                "id": "sign_off",
                "type": "approval",
                "approvers": ["reviewer"],
                # Reached through a branch, so it says itself where it goes next.
                "goto": "apply",
            },
            {"id": "apply", "type": "tool", "tool": "apply_it"},
        ],
    }
)

CALLS: list[str] = []


@tool("prepare_it")
def prepare_it(context):
    CALLS.append("prepare")
    return {"prepared": True}


@tool("apply_it")
def apply_it(context):
    CALLS.append("apply")
    return {"applied": True}


@tool("escalate_it")
def escalate_it(context):
    CALLS.append("escalated")
    return {"escalated": True}


@pytest.fixture
async def engine(store, dbos):
    CALLS.clear()
    running = Engine(
        Catalog(
            [NEEDS_A_YES, NOBODY_COMES, NOBODY_COMES_AND_NOWHERE_TO_GO, AFTER_A_BRANCH]
        ),
        store,
    )
    use_engine(running)
    return running


@pytest.fixture
def app(store):
    return create_app(
        catalog=Catalog(
            [NEEDS_A_YES, NOBODY_COMES, NOBODY_COMES_AND_NOWHERE_TO_GO, AFTER_A_BRANCH]
        ),
        store=store,
    )


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def step_of(instance, step_id: str):
    return next(step for step in instance.steps if step.step_id == step_id)


async def wait_for(store, scope, instance_id, ready) -> Any:
    read = await store.get(scope=scope, instance_id=instance_id)
    for _ in range(200):
        if ready(read):
            return read
        await asyncio.sleep(0.05)
        read = await store.get(scope=scope, instance_id=instance_id)
    return read


async def waiting_instance(store, scope, definition=NEEDS_A_YES):
    instance = await store.create(scope=scope, definition=definition, payload={})
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    await wait_for(
        store, scope, instance.id, lambda read: read.status == "waiting_approval"
    )
    return instance, handle


async def decide(
    instance_id: str, step_id: str, by: str, decision: str = "approved"
) -> None:
    await DBOS.send_async(
        destination_id=step_workflow_id(instance_id, step_id),
        message={"decision": decision, "by": by, "note": None},
        topic=approval_topic(step_id),
    )


async def test_an_approval_stops_the_instance_and_says_who_has_to_decide(
    engine, store, scope
):
    instance, handle = await waiting_instance(store, scope)

    read = await store.get(scope=scope, instance_id=instance.id)
    assert read.status == "waiting_approval"
    assert step_of(read, "sign_off").status == "waiting_approval"
    assert "reviewer" in step_of(read, "sign_off").question
    # Nothing downstream has run: the instance is stopped, not busy.
    assert CALLS == ["prepare"]

    await decide(str(instance.id), "sign_off", "reviewer")
    assert await handle.get_result() == "completed"


async def test_an_instance_that_waits_costs_nothing(engine, store, scope, pool):
    """The claim the whole design rests on, measured instead of asserted.

    While the step waits there is no workflow running and no connection held:
    every connection of the pool is back in it, and DBOS has the instance as
    PENDING rather than as something occupying a worker.
    """
    instance, handle = await waiting_instance(store, scope)

    stats = pool.get_stats()
    status = await DBOS.get_workflow_status_async(str(instance.id))

    assert stats["pool_available"] == stats["pool_size"]
    assert stats["requests_waiting"] == 0
    assert status.status == "PENDING"

    await decide(str(instance.id), "sign_off", "reviewer")
    assert await handle.get_result() == "completed"


async def test_the_decision_is_written_down_with_whoever_made_it(
    engine, app, store, scope
):
    instance, handle = await waiting_instance(store, scope)

    async with await client_for(app) as client:
        decided = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "reviewer", "note": "checked the numbers"},
            headers={"X-Process-Scope": scope},
        )

    assert decided.status_code == 200
    assert await handle.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    signed = step_of(read, "sign_off")
    # "The process went through here, and somebody let it" is the question the
    # history has to answer without anyone guessing.
    assert signed.output == {
        "decision": "approved",
        "by": "reviewer",
        # Nobody in front of this service vouched for the name: the record says so.
        "verified": False,
        "note": "checked the numbers",
    }
    assert CALLS == ["prepare", "apply"]


async def test_a_second_decision_on_the_same_step_does_nothing(
    engine, app, store, scope
):
    instance, handle = await waiting_instance(store, scope)

    async with await client_for(app) as client:
        first = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "reviewer"},
            headers={"X-Process-Scope": scope},
        )
        assert await handle.get_result() == "completed"
        again = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "reviewer", "decision": "rejected"},
            headers={"X-Process-Scope": scope},
        )

    assert first.status_code == 200
    # The second one is refused rather than kept: a decision left in the mailbox
    # would be read by whatever waits next.
    assert again.status_code == 409
    assert "not waiting" in again.json()["detail"]

    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "sign_off").output["decision"] == "approved"
    assert read.status == "completed"


async def test_only_the_declared_approvers_can_decide(engine, app, store, scope):
    instance, handle = await waiting_instance(store, scope)

    async with await client_for(app) as client:
        outsider = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "somebody-else"},
            headers={"X-Process-Scope": scope},
        )

    assert outsider.status_code == 403
    assert "reviewer" in outsider.json()["detail"]

    await decide(str(instance.id), "sign_off", "reviewer")
    assert await handle.get_result() == "completed"


async def test_a_refusal_stops_the_instance_in_a_state_that_says_so(
    engine, store, scope
):
    instance, handle = await waiting_instance(store, scope)

    await decide(str(instance.id), "sign_off", "reviewer", decision="rejected")

    # A no is an answer, not a crash: nothing downstream runs, and the state is
    # 'rejected' rather than 'failed'.
    assert await handle.get_result() == "rejected"
    read = await store.get(scope=scope, instance_id=instance.id)
    assert read.status == "rejected"
    assert step_of(read, "sign_off").output["decision"] == "rejected"
    assert step_of(read, "apply").status == "pending"
    assert CALLS == ["prepare"]


async def test_an_approval_on_a_branch_hands_control_to_the_step_it_names(
    engine, store, scope
):
    """A step reached through a branch cannot be waited on with depends_on.

    The other side of the branch would never run, so the dependency would never
    be satisfied: the step says itself where it goes.
    """
    instance = await store.create(
        scope=scope, definition=AFTER_A_BRANCH, payload={"amount": 5000}
    )
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    await wait_for(
        store, scope, instance.id, lambda read: read.status == "waiting_approval"
    )

    await decide(str(instance.id), "sign_off", "reviewer")
    assert await handle.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "apply").status == "completed"
    assert CALLS == ["apply"]


async def test_nobody_deciding_escalates_where_the_definition_says(
    engine, store, scope
):
    instance = await store.create(scope=scope, definition=NOBODY_COMES, payload={})

    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    result = await handle.get_result()

    read = await store.get(scope=scope, instance_id=instance.id)
    # Nobody decided, so the work went where the definition said it should go.
    assert result == "completed"
    assert step_of(read, "sign_off").status == "escalated"
    assert "by_the_manager" in step_of(read, "sign_off").note
    assert CALLS == ["escalated"]


async def test_a_wait_with_nowhere_to_escalate_fails_instead_of_hanging(
    engine, store, scope
):
    instance = await store.create(
        scope=scope, definition=NOBODY_COMES_AND_NOWHERE_TO_GO, payload={}
    )

    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    result = await handle.get_result()

    read = await store.get(scope=scope, instance_id=instance.id)
    # Without an escalation there is nobody left to ask: saying so is better
    # than an instance that stays 'waiting' with nothing coming.
    assert result == "failed"
    assert read.status == "failed"
    assert step_of(read, "sign_off").status == "failed"


WAITS_THEN_DIES = """
import asyncio, os, sys
sys.path.insert(0, "src")
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dbos import DBOS, SetWorkflowID
from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import (
    Engine,
    advance_instance,
    step_workflow_id,
    use_engine,
)
from process_service.store import InstanceStore, build_pool
from process_service.tools import tool

DEFINITION = parse_definition({
    "id": "needs-a-yes", "version": 1,
    "steps": [
        {"id": "prepare", "type": "tool", "tool": "prepare_it"},
        {"id": "sign_off", "type": "approval", "approvers": ["reviewer"],
         "depends_on": ["prepare"]},
        {"id": "apply", "type": "tool", "tool": "apply_it", "depends_on": ["sign_off"]},
    ],
})

@tool("prepare_it")
def prepare_it(context):
    return {"prepared": True}

@tool("apply_it")
def apply_it(context):
    return {"applied": True}

async def main():
    dsn = os.environ["PROCESS_POSTGRES_DSN"]
    pool = build_pool(dsn)
    await pool.open(wait=True)
    use_engine(Engine(Catalog([DEFINITION]), InstanceStore(pool)))
    DBOS(config={"name": "process-service-tests", "system_database_url": dsn,
                 "run_admin_server": False, "enable_otlp": False})
    DBOS.launch()
    instance_id = os.environ["INSTANCE_ID"]
    with SetWorkflowID(instance_id):
        handle = await DBOS.start_workflow_async(
            advance_instance, instance_id, os.environ["SCOPE"]
        )
    print("waiting", flush=True)
    await handle.get_result()

asyncio.run(main())
"""


async def test_the_approval_can_come_after_a_restart(engine, store, scope, tmp_path):
    """The approval arrives the day after, from another process, and it works.

    That is the whole reason for a durable wait: nobody approves within the
    lifetime of the request that asked, and the instance must not depend on the
    process that started it still being alive.
    """
    instance = await store.create(scope=scope, definition=NEEDS_A_YES, payload={})
    script = tmp_path / "waiter.py"
    script.write_text(textwrap.dedent(WAITS_THEN_DIES), encoding="utf-8")

    # S603 is a false positive here: the interpreter is this one and the script
    # is written by the test just above -- no external input reaches this line.
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env={
            **os.environ,
            "PROCESS_POSTGRES_DSN": POSTGRES_DSN or "",
            "INSTANCE_ID": str(instance.id),
            "SCOPE": scope,
        },
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "waiting"
        read = await wait_for(
            store, scope, instance.id, lambda read: read.status == "waiting_approval"
        )
    finally:
        child.kill()
        child.wait(timeout=30)

    assert step_of(read, "sign_off").status == "waiting_approval"

    await decide(str(instance.id), "sign_off", "reviewer")
    # A restarted service recovers every workflow left pending; here the two of
    # them are named, because the test is the one doing the recovering: the step
    # that was waiting, and the instance that was waiting for the step.
    await DBOS.resume_workflow_async(step_workflow_id(str(instance.id), "sign_off"))
    resumed = await DBOS.resume_workflow_async(str(instance.id))
    assert await resumed.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert read.status == "completed"
    assert step_of(read, "sign_off").output["by"] == "reviewer"
    assert step_of(read, "apply").status == "completed"


async def test_a_decision_without_an_identity_proxy_is_marked_unverified(
    engine, app, store, scope
):
    instance, handle = await waiting_instance(store, scope)

    async with await client_for(app) as client:
        decided = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "reviewer"},
            headers={"X-Process-Scope": scope},
        )

    assert decided.json() == {"state": "approved", "by": "reviewer", "verified": False}
    assert await handle.get_result() == "completed"


async def test_behind_an_identity_proxy_the_decider_is_who_the_proxy_says(
    engine, store, scope, monkeypatch
):
    monkeypatch.setenv("PROCESS_IDENTITY_HEADER", "X-Forwarded-User")
    app = create_app(catalog=Catalog([NEEDS_A_YES]), store=store)
    instance, handle = await waiting_instance(store, scope)

    async with await client_for(app) as client:
        impostor = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "reviewer"},
            headers={"X-Process-Scope": scope, "X-Forwarded-User": "mallory"},
        )
        genuine = await client.post(
            f"/instances/{instance.id}/steps/sign_off/decision",
            json={"by": "whatever the form says"},
            headers={"X-Process-Scope": scope, "X-Forwarded-User": "reviewer"},
        )

    # Typing an approver's name is not being one, once the deployment says who
    # is asking.
    assert impostor.status_code == 403
    assert genuine.json() == {"state": "approved", "by": "reviewer", "verified": True}
    assert await handle.get_result() == "completed"
    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "sign_off").output["verified"] is True
