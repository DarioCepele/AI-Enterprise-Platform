"""What survives a crash, and what must not happen twice."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from uuid import UUID

import pytest
from dbos import DBOS

from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import Engine, advance_instance, use_engine
from process_service.tools import tool

from conftest import POSTGRES_DSN, needs_postgres

pytestmark = [needs_postgres, pytest.mark.integration]

THREE_STEPS = parse_definition(
    {
        "id": "three-steps",
        "version": 1,
        "steps": [
            {"id": "one", "type": "tool", "tool": "count_one"},
            {"id": "two", "type": "tool", "tool": "count_two", "depends_on": ["one"]},
            {"id": "three", "type": "tool", "tool": "count_three", "depends_on": ["two"]},
        ],
    }
)

WITH_EFFECT = parse_definition(
    {
        "id": "with-effect",
        "version": 1,
        "steps": [
            {
                "id": "apply",
                "type": "tool",
                "tool": "count_one",
                "idempotency_key": "request_id",
            }
        ],
    }
)

BRANCHING = parse_definition(
    {
        "id": "branching",
        "version": 1,
        "steps": [
            {"id": "start", "type": "tool", "tool": "count_one"},
            {
                "id": "decide",
                "type": "decision",
                "depends_on": ["start"],
                "branches": [
                    {"when": "amount > 10000", "goto": "big"},
                    {"when": "true", "goto": "small"},
                ],
            },
            {"id": "big", "type": "tool", "tool": "count_two"},
            {"id": "small", "type": "tool", "tool": "count_three"},
        ],
    }
)

WITH_APPROVAL = parse_definition(
    {
        "id": "with-approval",
        "version": 1,
        "steps": [
            {"id": "before", "type": "tool", "tool": "count_one"},
            {
                "id": "sign_off",
                "type": "approval",
                "approvers": ["operations"],
                "depends_on": ["before"],
            },
            {"id": "after", "type": "tool", "tool": "count_two", "depends_on": ["sign_off"]},
        ],
    }
)

CALLS: list[str] = []


@tool("count_one")
def count_one(context):
    CALLS.append("one")
    return {"one": True}


@tool("count_two")
def count_two(context):
    CALLS.append("two")
    return {"two": True}


@tool("count_three")
def count_three(context):
    CALLS.append("three")
    return {"three": True}


@pytest.fixture
def catalog() -> Catalog:
    return Catalog([THREE_STEPS, WITH_EFFECT, BRANCHING, WITH_APPROVAL])


@pytest.fixture
async def engine(catalog, store, dbos):
    CALLS.clear()
    running = Engine(catalog, store)
    use_engine(running)
    return running


async def test_a_process_of_three_steps_runs_them_in_order(engine, store, scope):
    instance = await store.create(scope=scope, definition=THREE_STEPS, payload={})

    result = await advance_instance(str(instance.id), scope)

    assert result == "completed"
    assert CALLS == ["one", "two", "three"]
    assert (await store.get(scope=scope, instance_id=instance.id)).status == "completed"


async def test_the_steps_already_done_are_not_done_again_after_a_crash(engine, store, scope):
    """A workflow interrupted and resumed picks up where it stopped.

    Cancelling is how a test says "the process died here": what matters is that
    the resumed run reads the finished steps from the ledger instead of calling
    the tools again.
    """
    instance = await store.create(scope=scope, definition=THREE_STEPS, payload={})
    handle = await DBOS.start_workflow_async(advance_instance, str(instance.id), scope)
    await handle.get_result()

    recorded = await DBOS.list_workflow_steps_async(handle.workflow_id)
    tool_calls = [step for step in recorded if step["function_name"].endswith("run_tool_step")]
    restart_from = tool_calls[-1]["function_id"]
    CALLS.clear()

    resumed = await DBOS.fork_workflow_async(handle.workflow_id, start_step=restart_from)
    await resumed.get_result()

    # Only the last tool ran again: the two before it were read from the ledger,
    # which is what stops a recovered process from doing its work twice.
    assert CALLS == ["three"]


async def test_an_effect_is_applied_once_even_if_the_step_runs_twice(engine, store, scope):
    instance = await store.create(
        scope=scope, definition=WITH_EFFECT, payload={"request_id": "r-1"}
    )

    await advance_instance(str(instance.id), scope)
    CALLS.clear()
    # The same instance advanced again: the second attempt must find the effect
    # already recorded and not repeat it.
    await advance_instance(str(instance.id), scope)

    assert CALLS == []
    assert len(await store.effects_of(instance_id=instance.id)) == 1


async def test_a_decision_takes_one_branch_and_writes_down_why(engine, store, scope):
    instance = await store.create(
        scope=scope, definition=BRANCHING, payload={"amount": 25000}
    )

    await advance_instance(str(instance.id), scope)

    read = await store.get(scope=scope, instance_id=instance.id)
    decided = next(step for step in read.steps if step.step_id == "decide")
    assert CALLS == ["one", "two"]
    assert decided.output == {"when": "amount > 10000", "goto": "big"}
    assert next(step for step in read.steps if step.step_id == "small").status == "pending"


async def test_the_other_branch_runs_when_the_rule_says_so(engine, store, scope):
    instance = await store.create(scope=scope, definition=BRANCHING, payload={"amount": 10})

    await advance_instance(str(instance.id), scope)

    assert CALLS == ["one", "three"]


async def test_a_step_waiting_for_a_person_suspends_instead_of_failing(engine, store, scope):
    """Approvals are not implemented yet, and an instance still has to survive one.

    Waiting is a state, not a failure: what resumes the step arrives with a
    later block, and until then the instance is a row, not a held request. The
    agent step, which waits the same way, is tested in test_agent_steps.py.
    """
    instance = await store.create(scope=scope, definition=WITH_APPROVAL, payload={})

    result = await advance_instance(str(instance.id), scope)

    read = await store.get(scope=scope, instance_id=instance.id)
    assert result == "waiting"
    assert read.status == "waiting"
    assert next(step for step in read.steps if step.step_id == "sign_off").status == "waiting"
    assert CALLS == ["one"]


async def test_an_instance_keeps_running_its_own_version(engine, store, scope, catalog):
    instance = await store.create(scope=scope, definition=THREE_STEPS, payload={})
    revised = parse_definition(
        {
            "id": "three-steps",
            "version": 2,
            "steps": [{"id": "only", "type": "tool", "tool": "count_two"}],
        }
    )
    use_engine(Engine(Catalog([THREE_STEPS, revised]), store))

    await advance_instance(str(instance.id), scope)

    # The catalogue moved on while the instance was running: it has to finish
    # the process it started, not the one that exists now.
    assert CALLS == ["one", "two", "three"]


RESTART_SCRIPT = """
import asyncio, os, sys, uuid
sys.path.insert(0, "src")
sys.path.insert(0, "tests")
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dbos import DBOS, DBOSConfig, SetWorkflowID
from process_service.catalog import Catalog
from process_service.definitions import parse_definition
from process_service.engine import Engine, advance_instance, use_engine
from process_service.store import InstanceStore, build_pool
from process_service.tools import tool

DEFINITION = parse_definition({
    "id": "slow", "version": 1,
    "steps": [
        {"id": "quick", "type": "tool", "tool": "quick"},
        {"id": "endless", "type": "tool", "tool": "endless", "depends_on": ["quick"]},
    ],
})

@tool("quick")
def quick(context):
    return {"quick": True}

@tool("endless")
def endless(context):
    # The process is killed while this runs: the step never finishes, the one
    # before it did.
    import time
    time.sleep(600)
    return {}

async def main():
    dsn = os.environ["PROCESS_POSTGRES_DSN"]
    pool = build_pool(dsn)
    await pool.open(wait=True)
    store = InstanceStore(pool)
    use_engine(Engine(Catalog([DEFINITION]), store))
    DBOS(config={"name": "process-service", "system_database_url": dsn,
                 "run_admin_server": False, "enable_otlp": False})
    DBOS.launch()
    instance_id = os.environ["INSTANCE_ID"]
    print("started", flush=True)
    with SetWorkflowID(instance_id):
        await advance_instance(instance_id, os.environ["SCOPE"])

asyncio.run(main())
"""


async def test_an_instance_survives_the_death_of_the_process(engine, store, scope, tmp_path):
    """The real thing: a process killed mid-step, and recovery from the outside."""
    instance = await store.create(
        scope=scope,
        definition=parse_definition(
            {
                "id": "slow",
                "version": 1,
                "steps": [
                    {"id": "quick", "type": "tool", "tool": "quick"},
                    {"id": "endless", "type": "tool", "tool": "endless", "depends_on": ["quick"]},
                ],
            }
        ),
        payload={},
    )
    script = tmp_path / "runner.py"
    script.write_text(textwrap.dedent(RESTART_SCRIPT), encoding="utf-8")

    child = subprocess.Popen(
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
        assert child.stdout.readline().strip() == "started"
        await asyncio.sleep(4)
    finally:
        child.kill()
        child.wait(timeout=30)

    read = await store.get(scope=scope, instance_id=instance.id)
    quick_step = next(step for step in read.steps if step.step_id == "quick")
    endless_step = next(step for step in read.steps if step.step_id == "endless")

    # The first step is written down; the one that was running when the process
    # died is not. That difference is what recovery reads.
    assert quick_step.status == "completed"
    assert endless_step.status != "completed"
