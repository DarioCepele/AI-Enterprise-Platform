"""Delegating a step to an agent: the wait, the answer, and what goes wrong.

An agent step is the one that can take hours, so every test here is about the
instance being a row in the meantime -- and about the answer finding it again,
whatever happened to the process that asked the question.
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

from process_service.agents import summary_of, token_for
from process_service.api import create_app
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

DELEGATES = parse_definition(
    {
        "id": "delegates",
        "version": 1,
        "steps": [
            {"id": "collect", "type": "tool", "tool": "note_start"},
            {
                "id": "ask",
                "type": "agent",
                "owner": "knowledge",
                "depends_on": ["collect"],
                "input": {"question": "What does the policy say?"},
            },
            {"id": "close", "type": "tool", "tool": "note_end", "depends_on": ["ask"]},
        ],
    }
)

IMPATIENT = parse_definition(
    {
        "id": "impatient",
        "version": 1,
        "steps": [
            {
                "id": "ask",
                "type": "agent",
                "owner": "knowledge",
                "timeout_seconds": 1,
                "on_timeout": "by_hand",
                "input": {"question": "Anyone there?"},
            },
            {"id": "by_hand", "type": "tool", "tool": "note_by_hand"},
        ],
    }
)

CALLS: list[str] = []


@tool("note_start")
def note_start(context):
    CALLS.append("collect")
    return {"collected": True}


@tool("note_end")
def note_end(context):
    CALLS.append("close")
    return {"closed": True}


@tool("note_by_hand")
def note_by_hand(context):
    CALLS.append("by_hand")
    return {"by_hand": True}


class FakeAgent:
    """A gateway that accepts the task and never answers by itself.

    That is exactly the interesting shape: the answer only ever arrives on the
    webhook, so the tests drive the notification the way the real agent would.
    """

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.answered: list[dict[str, Any]] = []
        self.read_back: list[str] = []

    def knows(self, name: str) -> bool:
        return name == "knowledge"

    def known(self) -> list[str]:
        return ["knowledge"]

    async def ask(
        self, *, agent: str, question: str, scope: str, instance_id: str, step_id: str
    ):
        self.asked.append({"agent": agent, "question": question, "step_id": step_id})
        return {
            "task_id": f"task-{len(self.asked)}",
            "context_id": f"conversation-{len(self.asked)}",
            "state": "TASK_STATE_SUBMITTED",
        }

    async def reply(
        self,
        *,
        agent: str,
        answer: str,
        scope: str,
        instance_id: str,
        step_id: str,
        task_id: str,
        context_id: str = "",
    ):
        self.answered.append(
            {"answer": answer, "task_id": task_id, "context_id": context_id}
        )
        return {"task_id": task_id, "state": "TASK_STATE_WORKING"}

    async def result_of(self, *, agent: str, task_id: str) -> dict[str, Any]:
        self.read_back.append(task_id)
        return {
            "text": f"the answer of {task_id}",
            "usage": {"rounds": 2, "input_tokens": 1000, "output_tokens": 120},
        }


@pytest.fixture
def agents() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
async def engine(store, agents, dbos):
    CALLS.clear()
    running = Engine(Catalog([DELEGATES, IMPATIENT]), store, agents)
    use_engine(running)
    return running


def completion(task_id: str, text: str) -> dict[str, Any]:
    return {
        "task": {
            "id": task_id,
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"text": text}]}],
        }
    }


def clarification(task_id: str, text: str) -> dict[str, Any]:
    return {
        "statusUpdate": {
            "taskId": task_id,
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {"parts": [{"text": text}]},
            },
        }
    }


async def answer(instance_id: str, step_id: str, notification: dict[str, Any]) -> None:
    """What the webhook does, without the HTTP in the way."""
    task_id, state, text = summary_of(notification)
    await DBOS.send_async(
        destination_id=step_workflow_id(instance_id, step_id),
        message={"task_id": task_id, "state": state, "text": text},
        topic=step_id,
    )


async def wait_for(store, scope, instance_id, ready) -> Any:
    """Polls the instance until it looks the way the test needs it."""
    read = await store.get(scope=scope, instance_id=instance_id)
    for _ in range(200):
        if ready(read):
            return read
        await asyncio.sleep(0.05)
        read = await store.get(scope=scope, instance_id=instance_id)
    return read


def step_of(instance, step_id: str):
    return next(step for step in instance.steps if step.step_id == step_id)


async def running_instance(store, scope, definition=DELEGATES, payload=None):
    """Starts an instance and waits until its agent step is really waiting."""
    instance = await store.create(
        scope=scope, definition=definition, payload=payload or {}
    )
    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    await wait_for(
        store, scope, instance.id, lambda read: step_of(read, "ask").status == "waiting"
    )
    return instance, handle


async def test_an_agent_step_asks_once_and_writes_down_the_task(
    engine, store, scope, agents
):
    instance, handle = await running_instance(store, scope)

    read = await store.get(scope=scope, instance_id=instance.id)

    assert agents.asked[0]["question"] == "What does the policy say?"
    assert step_of(read, "ask").status == "waiting"
    assert step_of(read, "ask").task_id == "task-1"
    # The step after the agent has not run: the instance is waiting, not busy.
    assert CALLS == ["collect"]

    await answer(str(instance.id), "ask", completion("task-1", "the policy says yes"))
    assert await handle.get_result() == "completed"
    assert len(agents.asked) == 1


async def test_the_answer_resumes_the_instance_and_the_process_goes_on(
    engine, store, scope
):
    instance, handle = await running_instance(store, scope)

    await answer(str(instance.id), "ask", completion("task-1", "the policy says yes"))
    assert await handle.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "ask").status == "completed"
    # The answer comes from the task, not from the notification that woke the
    # step: the notification is a signal, and the fake agent's task says this.
    assert step_of(read, "ask").output["text"] == "the answer of task-1"
    assert CALLS == ["collect", "close"]


async def test_a_notification_without_the_text_makes_the_step_read_the_task(
    engine, store, scope, agents
):
    instance, handle = await running_instance(store, scope)

    # A notification is a signal, not the answer: this one says the task is done
    # and carries nothing, which is what happens when the artifacts were
    # notified separately.
    await answer(
        str(instance.id),
        "ask",
        {
            "statusUpdate": {
                "taskId": "task-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
            }
        },
    )
    assert await handle.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert agents.read_back == ["task-1"]
    assert step_of(read, "ask").output["text"] == "the answer of task-1"

    # What the answer cost is part of the history, not of the log.
    spent = [
        event
        for event in await store.events_of(instance_id=instance.id)
        if event.kind == "step_usage"
    ]
    assert [event.data["agent"] for event in spent] == ["knowledge"]
    assert spent[0].data["input_tokens"] == 1000


async def test_a_clarification_puts_the_step_in_front_of_a_person(
    engine, app, store, scope, agents
):
    instance, handle = await running_instance(store, scope)

    await answer(str(instance.id), "ask", clarification("task-1", "For which year?"))
    read = await wait_for(
        store, scope, instance.id, lambda read: read.status == "waiting_human"
    )

    # The round is not lost: the question is written where somebody can read it,
    # and the same step keeps waiting for the answer to it.
    assert step_of(read, "ask").status == "waiting_human"
    assert step_of(read, "ask").question == "For which year?"

    async with await client_for(app) as client:
        answered = await client.post(
            f"/instances/{instance.id}/steps/ask/answer",
            json={"text": "2026"},
            headers={"X-Process-Scope": scope},
        )
    assert answered.status_code == 200

    # The answer went back into the task that asked, not into a new one: the
    # agent keeps what it had already worked out.
    for _ in range(200):
        if agents.answered:
            break
        await asyncio.sleep(0.05)
    # The answer goes back into the same task **and** the same conversation:
    # without the second one the agent refuses it as belonging somewhere else.
    assert agents.answered == [
        {"answer": "2026", "task_id": "task-1", "context_id": "conversation-1"}
    ]
    assert len(agents.asked) == 1

    await answer(str(instance.id), "ask", completion("task-1", "in 2026, yes"))
    assert await handle.get_result() == "completed"
    assert CALLS == ["collect", "close"]


async def test_an_answer_is_refused_where_nothing_was_asked(engine, app, store, scope):
    instance, handle = await running_instance(store, scope)

    async with await client_for(app) as client:
        too_early = await client.post(
            f"/instances/{instance.id}/steps/ask/answer",
            json={"text": "nobody asked"},
            headers={"X-Process-Scope": scope},
        )
        nowhere = await client.post(
            f"/instances/{instance.id}/steps/not-a-step/answer",
            json={"text": "nobody asked"},
            headers={"X-Process-Scope": scope},
        )

    assert too_early.status_code == 409
    assert "waiting" in too_early.json()["detail"]
    assert nowhere.status_code == 404

    await answer(str(instance.id), "ask", completion("task-1", "done anyway"))
    assert await handle.get_result() == "completed"


async def test_a_step_that_nobody_answers_escalates_where_the_definition_says(
    engine, store, scope
):
    instance = await store.create(scope=scope, definition=IMPATIENT, payload={})

    with SetWorkflowID(str(instance.id)):
        handle = await DBOS.start_workflow_async(
            advance_instance, str(instance.id), scope
        )
    result = await handle.get_result()

    read = await store.get(scope=scope, instance_id=instance.id)
    # The escalation is not a note on a dead instance: the work went to the step
    # the definition named, and the process carried on from there.
    assert result == "completed"
    assert step_of(read, "ask").status == "escalated"
    assert "by_hand" in step_of(read, "ask").note
    assert step_of(read, "by_hand").status == "completed"
    assert CALLS == ["by_hand"]


@pytest.fixture
def app(store, agents):
    return create_app(
        catalog=Catalog([DELEGATES, IMPATIENT]), store=store, agents=agents
    )


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_the_webhook_refuses_a_notification_that_is_not_signed(app, store, scope):
    instance = await store.create(scope=scope, definition=DELEGATES, payload={})

    async with await client_for(app) as client:
        without = await client.post(
            f"/a2a/push/{scope}/{instance.id}/ask", json=completion("task-1", "hello")
        )
        wrong = await client.post(
            f"/a2a/push/{scope}/{instance.id}/ask",
            json=completion("task-1", "hello"),
            headers={
                "X-A2A-Notification-Token": token_for(
                    scope, str(instance.id), "another-step"
                )
            },
        )

    # A token good for one step is not good for another: the signature covers
    # scope, instance and step, so a leaked token cannot be pointed elsewhere.
    assert without.status_code == 403
    assert wrong.status_code == 403


async def test_a_token_does_not_cross_scopes(app, store, scope):
    instance = await store.create(scope=scope, definition=DELEGATES, payload={})
    elsewhere = token_for(f"{scope}-other", str(instance.id), "ask")

    async with await client_for(app) as client:
        crossed = await client.post(
            f"/a2a/push/{scope}/{instance.id}/ask",
            json=completion("task-1", "hello"),
            headers={"X-A2A-Notification-Token": elsewhere},
        )

    assert crossed.status_code == 403


async def test_the_webhook_wakes_the_instance_and_ignores_progress(
    engine, app, store, scope
):
    instance, handle = await running_instance(store, scope)
    headers = {"X-A2A-Notification-Token": token_for(scope, str(instance.id), "ask")}

    async with await client_for(app) as client:
        progress = await client.post(
            f"/a2a/push/{scope}/{instance.id}/ask",
            json={
                "statusUpdate": {
                    "taskId": "task-1",
                    "status": {"state": "TASK_STATE_WORKING"},
                }
            },
            headers=headers,
        )
        done = await client.post(
            f"/a2a/push/{scope}/{instance.id}/ask",
            json=completion("task-1", "here it is"),
            headers=headers,
        )

    assert progress.json() == {"state": "progress ignored"}
    assert done.json() == {"state": "received"}
    assert await handle.get_result() == "completed"


ASKS_THEN_DIES = """
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
    "id": "delegates", "version": 1,
    "steps": [
        {"id": "collect", "type": "tool", "tool": "note_start"},
        {"id": "ask", "type": "agent", "owner": "knowledge", "depends_on": ["collect"],
         "input": {"question": "What does the policy say?"}},
        {"id": "close", "type": "tool", "tool": "note_end", "depends_on": ["ask"]},
    ],
})

@tool("note_start")
def note_start(context):
    return {"collected": True}

@tool("note_end")
def note_end(context):
    return {"closed": True}

class FakeAgent:
    def knows(self, name):
        return True

    def known(self):
        return ["knowledge"]

    async def ask(self, *, agent, question, scope, instance_id, step_id):
        return {"task_id": "task-1", "state": "TASK_STATE_SUBMITTED"}

async def main():
    dsn = os.environ["PROCESS_POSTGRES_DSN"]
    pool = build_pool(dsn)
    await pool.open(wait=True)
    use_engine(Engine(Catalog([DEFINITION]), InstanceStore(pool), FakeAgent()))
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


async def test_the_answer_finds_the_instance_after_the_asking_process_died(
    engine, store, scope, tmp_path
):
    """The point of the whole design: the wait outlives the process.

    A process starts the instance, asks the agent and is killed while waiting.
    The notification arrives at a different process, and the instance -- resumed
    from the ledger -- reads the answer and finishes.
    """
    instance = await store.create(scope=scope, definition=DELEGATES, payload={})
    script = tmp_path / "asker.py"
    script.write_text(textwrap.dedent(ASKS_THEN_DIES), encoding="utf-8")

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
            store,
            scope,
            instance.id,
            lambda read: step_of(read, "ask").status == "waiting",
        )
    finally:
        child.kill()
        child.wait(timeout=30)

    assert step_of(read, "ask").task_id == "task-1"

    await answer(
        str(instance.id), "ask", completion("task-1", "answered after the crash")
    )
    # A restarted service recovers every workflow left pending; here the two of
    # them are named, because the test is the one doing the recovering: the step
    # that was waiting, and the instance that was waiting for the step.
    await DBOS.resume_workflow_async(step_workflow_id(str(instance.id), "ask"))
    resumed = await DBOS.resume_workflow_async(str(instance.id))
    assert await resumed.get_result() == "completed"

    read = await store.get(scope=scope, instance_id=instance.id)
    assert step_of(read, "ask").output["text"] == "the answer of task-1"
    assert read.status == "completed"
