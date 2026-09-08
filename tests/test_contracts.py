"""What this service puts on the wire, checked against the shared contracts."""
from __future__ import annotations

import httpx
import pytest

from contracts import assert_shape, load, sample
from memory_service.api import create_app
from memory_service.models import Snapshot
from memory_service.service import ThreadMemory

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]

HEADERS_OF = staticmethod(lambda scope: {"X-Memory-Scope": scope})


@pytest.fixture
def memory(transcripts, hot) -> ThreadMemory:
    return ThreadMemory(transcripts, hot)


async def client_for(memory: ThreadMemory) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(memory=memory))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_saving_a_snapshot_answers_with_the_declared_counter(memory, scope):
    async with await client_for(memory) as client:
        saved = await client.put(
            "/threads/t1/snapshot",
            json={"messages": sample("memory/snapshot")["messages"]},
            headers={"X-Memory-Scope": scope},
        )

    assert saved.status_code == 200
    assert_shape("memory/write-results", {**sample("memory/write-results"), "save_snapshot": saved.json()})


async def test_the_snapshot_read_back_matches_the_contract(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.put(
            "/threads/t1/snapshot",
            json={
                "messages": sample("memory/snapshot")["messages"],
                "state": sample("agui/shared-state"),
            },
            headers=headers,
        )
        read = await client.get("/threads/t1/snapshot", headers=headers)

    assert read.status_code == 200
    assert_shape("memory/snapshot", read.json())


async def test_the_shared_state_comes_back_as_it_went_in(memory, scope):
    state = sample("agui/shared-state")

    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.put(
            "/threads/t1/snapshot", json={"messages": [], "state": state}, headers=headers
        )
        read = await client.get("/threads/t1/snapshot", headers=headers)

    # This service does not read the shared state, it keeps it. Changing its
    # shape upstream must not require a change here -- this test is what says so.
    assert read.json()["state"] == state


async def test_the_search_answer_matches_the_contract(memory, scope):
    async with await client_for(memory) as client:
        found = await client.post(
            "/search", json={"query": "anything"}, headers={"X-Memory-Scope": scope}
        )

    assert found.status_code == 200
    # Without an embedder the list is empty; the contract is the envelope, and an
    # empty list still has to arrive under the declared key.
    assert list(found.json()) == list(sample("memory/search"))


async def test_forgetting_answers_with_the_declared_counters(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.put("/threads/t1/snapshot", json={"messages": []}, headers=headers)
        thread = await client.delete("/threads/t1", headers=headers)
        whole_scope = await client.delete("/scope", headers=headers)

    counters = sample("memory/write-results")
    assert list(thread.json()) == list(counters["forget_thread"])
    assert list(whole_scope.json()) == list(counters["forget_scope"])


def test_the_contracts_name_this_repository_as_the_producer():
    assert "demo-memory-service" in load("memory/snapshot")["produced_by"]
    assert "demo-memory-service" in load("memory/search")["produced_by"]
    assert "demo-memory-service" in load("agui/shared-state")["consumed_by"]
