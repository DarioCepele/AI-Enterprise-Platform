"""Instances: rows that outlive the process that wrote them."""
from __future__ import annotations

import httpx
import pytest
from uuid import uuid4

from process_service.api import create_app
from process_service.catalog import load_catalog
from process_service.definitions import parse_definition
from process_service.migrations import LATEST_VERSION, applied_versions, run_migrations

from conftest import needs_postgres

pytestmark = [needs_postgres, pytest.mark.integration]

SIMPLE = parse_definition(
    {
        "id": "simple",
        "version": 1,
        "steps": [
            {"id": "first", "type": "tool", "tool": "do"},
            {"id": "second", "type": "agent", "owner": "knowledge", "depends_on": ["first"]},
        ],
    }
)

REVISED = parse_definition(
    {
        "id": "simple",
        "version": 2,
        "steps": [{"id": "only", "type": "tool", "tool": "do_differently"}],
    }
)


async def test_the_migrations_are_recorded_and_idempotent(pool):
    async with pool.connection() as connection:
        assert await applied_versions(connection) == {
            version for version in range(1, LATEST_VERSION + 1)
        }
        assert await run_migrations(connection) == []


async def test_starting_an_instance_writes_it_with_its_steps(store, scope):
    instance = await store.create(scope=scope, definition=SIMPLE, payload={"amount": 12})

    assert instance.process_id == "simple"
    assert instance.process_version == 1
    assert instance.status == "pending"
    assert instance.input == {"amount": 12}
    assert [step.step_id for step in instance.steps] == ["first", "second"]
    assert [step.owner for step in instance.steps] == [None, "knowledge"]


async def test_an_instance_keeps_the_version_it_started_with(store, scope):
    old = await store.create(scope=scope, definition=SIMPLE, payload={})
    new = await store.create(scope=scope, definition=REVISED, payload={})

    # The catalogue moves on; an instance has to keep finishing the process it
    # started, or it would end following one nobody launched.
    assert (await store.get(scope=scope, instance_id=old.id)).process_version == 1
    assert (await store.get(scope=scope, instance_id=new.id)).process_version == 2


async def test_an_instance_of_another_scope_is_not_readable(store, scope):
    mine = await store.create(scope=scope, definition=SIMPLE, payload={})

    assert await store.get(scope="somebody-else", instance_id=mine.id) is None


async def test_an_unknown_instance_reads_as_nothing(store, scope):
    assert await store.get(scope=scope, instance_id=uuid4()) is None


async def test_the_list_is_newest_first_and_filtered_by_state(store, scope):
    first = await store.create(scope=scope, definition=SIMPLE, payload={})
    second = await store.create(scope=scope, definition=SIMPLE, payload={})
    await store.set_status(instance_id=first.id, status="running")

    everything = await store.list(scope=scope)
    running = await store.list(scope=scope, status="running")

    assert [instance.id for instance in everything] == [second.id, first.id]
    assert [instance.id for instance in running] == [first.id]


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def app(store):
    return create_app(catalog=load_catalog("processes"), store=store)


async def test_the_api_lists_what_can_be_started(app):
    async with await client_for(app) as client:
        answer = await client.get("/processes")

    assert answer.status_code == 200
    assert "example-approval" in {item["id"] for item in answer.json()["processes"]}


async def test_the_api_starts_and_reads_back_an_instance(app, scope):
    headers = {"X-Process-Scope": scope}

    async with await client_for(app) as client:
        started = await client.post(
            "/processes/example-approval/instances",
            json={"input": {"amount": 25000, "request_id": "r-1"}},
            headers=headers,
        )
        read = await client.get(f"/instances/{started.json()['id']}", headers=headers)

    assert started.status_code == 201
    assert read.status_code == 200
    assert read.json()["input"]["amount"] == 25000
    assert read.json()["process_version"] == 1


async def test_the_api_refuses_a_process_that_does_not_exist(app, scope):
    async with await client_for(app) as client:
        answer = await client.post(
            "/processes/nothing-like-this/instances",
            json={"input": {}},
            headers={"X-Process-Scope": scope},
        )

    assert answer.status_code == 404
    assert "example-approval" in answer.json()["detail"]


async def test_the_api_does_not_show_instances_of_another_scope(app, scope):
    async with await client_for(app) as client:
        started = await client.post(
            "/processes/example-approval/instances",
            json={"input": {}},
            headers={"X-Process-Scope": scope},
        )
        mine = await client.get("/instances", headers={"X-Process-Scope": scope})
        theirs = await client.get("/instances", headers={"X-Process-Scope": "somebody-else"})

    assert started.json()["id"] in {item["id"] for item in mine.json()["instances"]}
    assert started.json()["id"] not in {item["id"] for item in theirs.json()["instances"]}


async def test_readiness_counts_the_definitions(app):
    async with await client_for(app) as client:
        ready = await client.get("/health/ready")
        alive = await client.get("/health/live")

    assert ready.json()["processes"] >= 1
    assert alive.json() == {"status": "alive"}
