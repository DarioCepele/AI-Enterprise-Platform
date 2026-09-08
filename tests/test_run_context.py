"""Il piano segue il thread, non il processo."""
from __future__ import annotations

import json

import httpx
import pytest

from demo.agents.master import build_master_agent
from demo.chat_clients.fake import ToolCallingFakeClient
from demo.plan import PlanStore
from demo.server.app import create_app
from demo.server.run_context import LabRunner, current_plan, plan_from_state

PIANO = {
    "status": "in_progress",
    "steps": [
        {
            "id": 1,
            "title": "Primo passo",
            "detail": "",
            "source": "",
            "status": "pending",
            "started_at": None,
            "ended_at": None,
            "note": None,
        }
    ],
}


def test_the_plan_is_hydrated_from_the_shared_state():
    store = plan_from_state({"state": {"plan": PIANO}})

    assert store.snapshot()["steps"][0]["title"] == "Primo passo"
    assert store.snapshot()["status"] == "in_progress"


def test_a_request_without_a_plan_starts_from_an_empty_one():
    assert plan_from_state({}).snapshot() == {"status": "idle", "steps": []}
    assert plan_from_state({"state": {}}).snapshot() == {"status": "idle", "steps": []}


def test_a_malformed_plan_does_not_crash_the_run():
    store = plan_from_state({"state": {"plan": {"status": 3, "steps": "non una lista"}}})

    assert store.snapshot()["steps"] == []


def test_the_hydrated_plan_is_a_copy_not_a_live_reference():
    originale = json.loads(json.dumps(PIANO))
    store = plan_from_state({"state": {"plan": originale}})

    store.set_status(1, "completed", None)

    assert originale["steps"][0]["status"] == "pending"


class Runner(LabRunner):
    def __init__(self, visto: list) -> None:
        super().__init__(agent=None, state_loader=None)
        self._visto = visto

    async def _framework_events(self, input_data):
        self._visto.append(current_plan.get())
        return
        yield


@pytest.mark.asyncio
async def test_every_run_sees_its_own_plan():
    visto: list = []
    runner = Runner(visto)

    async for _ in runner.run({"state": {"plan": PIANO}}):
        pass
    async for _ in runner.run({"state": {}}):
        pass

    assert [len(p.snapshot()["steps"]) for p in visto] == [1, 0]


@pytest.mark.asyncio
async def test_outside_a_run_no_plan_leaks_into_the_process():
    assert current_plan.get() is None


@pytest.mark.asyncio
async def test_a_second_run_does_not_inherit_the_first_plan():
    async def run_once(thread_id: str, tool_name: str, tool_args: dict) -> list[dict]:
        app = create_app(
            agent=build_master_agent(
                chat_client=ToolCallingFakeClient(
                    tool_name=tool_name, tool_args=tool_args, final_text="fatto"
                )
            )
        )
        request = {
            "threadId": thread_id,
            "runId": f"r-{thread_id}",
            "state": {},
            "messages": [{"id": f"m-{thread_id}", "role": "user", "content": "lavora"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST", "/agui", json=request, headers={"Accept": "text/event-stream"}
            ) as response:
                return [
                    json.loads(line[len("data: "):])
                    async for line in response.aiter_lines()
                    if line.startswith("data: ")
                ]

    await run_once(
        "t1", "todo_write", {"steps": [{"id": 1, "title": "Passo", "detail": "", "source": ""}]}
    )
    eventi = await run_once("t2", "todo_set_status", {"step_id": 1, "status": "completed"})

    piani = [
        e["snapshot"]["plan"]
        for e in eventi
        if e["type"] == "STATE_SNAPSHOT" and "plan" in e.get("snapshot", {})
    ]
    completati = [
        passo
        for piano in piani
        for passo in piano["steps"]
        if passo["status"] == "completed"
    ]
    assert completati == [], "il piano del primo thread e' sopravvissuto nel processo"


def test_the_store_still_works_standalone():
    store = PlanStore()
    store.write([{"id": 1, "title": "Passo", "detail": "", "source": ""}])

    assert store.snapshot()["steps"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_a_shared_store_does_leak_which_is_why_it_is_not_the_default():
    condiviso = PlanStore()

    async def run_once(thread_id: str, tool_name: str, tool_args: dict) -> list[dict]:
        app = create_app(
            agent=build_master_agent(
                chat_client=ToolCallingFakeClient(
                    tool_name=tool_name, tool_args=tool_args, final_text="fatto"
                ),
                plan_store=condiviso,
            )
        )
        request = {
            "threadId": thread_id,
            "runId": f"r-{thread_id}",
            "state": {},
            "messages": [{"id": f"m-{thread_id}", "role": "user", "content": "lavora"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST", "/agui", json=request, headers={"Accept": "text/event-stream"}
            ) as response:
                return [
                    json.loads(line[len("data: "):])
                    async for line in response.aiter_lines()
                    if line.startswith("data: ")
                ]

    await run_once(
        "t1", "todo_write", {"steps": [{"id": 1, "title": "Passo", "detail": "", "source": ""}]}
    )
    eventi = await run_once("t2", "todo_set_status", {"step_id": 1, "status": "completed"})

    completati = [
        passo
        for e in eventi
        if e["type"] == "STATE_SNAPSHOT" and "plan" in e.get("snapshot", {})
        for passo in e["snapshot"]["plan"]["steps"]
        if passo["status"] == "completed"
    ]
    assert completati, "senza store condiviso questo test non dimostrerebbe nulla"
