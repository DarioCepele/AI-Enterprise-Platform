"""L'indice delle memorie, contro pgvector vero."""
from __future__ import annotations

import pytest
from conftest import needs_backends

from memory_service.stores.vectors import PostgresMemories

pytestmark = [needs_backends, pytest.mark.integration]


@pytest.fixture
async def memories(pool, scope: str) -> PostgresMemories:
    store = PostgresMemories(pool)
    yield store
    await store.forget_scope(scope)


async def test_the_nearest_memory_comes_first(memories, scope):
    await memories.index(
        scope,
        [("t1", 1, "let us talk about Go"), ("t1", 2, "carbonara recipe")],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )

    found = await memories.search(scope, [0.9, 0.1, 0.0], limit=2)

    assert [m.text for m in found] == ["let us talk about Go", "carbonara recipe"]
    assert found[0].similarity > found[1].similarity


async def test_a_memory_carries_where_it_came_from(memories, scope):
    await memories.index(
        scope, [("old-thread", 42, "the contact is Marta")], [[1.0, 0.0, 0.0]]
    )

    found_one = (await memories.search(scope, [1.0, 0.0, 0.0], limit=1))[0]

    assert found_one.thread_id == "old-thread"
    assert found_one.seq == 42


async def test_an_empty_index_finds_nothing_instead_of_failing(memories, scope):
    assert await memories.search(scope, [1.0, 0.0, 0.0], limit=5) == []


async def test_scopes_do_not_see_each_other(memories, pool, scope):
    altrui = PostgresMemories(pool)
    await memories.index(scope, [("t1", 1, "riservato")], [[1.0, 0.0, 0.0]])
    try:
        assert await altrui.search(f"{scope}-other", [1.0, 0.0, 0.0], limit=5) == []
    finally:
        await altrui.forget_scope(f"{scope}-other")


async def test_the_limit_is_respected(memories, scope):
    await memories.index(
        scope,
        [("t1", i, f"memory {i}") for i in range(5)],
        [[1.0, i / 10, 0.0] for i in range(5)],
    )

    assert len(await memories.search(scope, [1.0, 0.0, 0.0], limit=2)) == 2


async def test_forgetting_a_thread_removes_its_memories(memories, scope):
    await memories.index(
        scope,
        [("t1", 1, "di t1"), ("t2", 1, "di t2")],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )

    removed = await memories.forget_thread(scope, "t1", [1])

    assert removed == 1
    found = await memories.search(scope, [1.0, 0.0, 0.0], limit=5)
    assert [m.text for m in found] == ["di t2"]


async def test_reindexing_the_same_memory_does_not_duplicate_it(memories, scope):
    await memories.index(scope, [("t1", 1, "prima versione")], [[1.0, 0.0, 0.0]])
    await memories.index(scope, [("t1", 1, "seconda versione")], [[1.0, 0.0, 0.0]])

    assert await memories.count(scope) == 1
    found = await memories.search(scope, [1.0, 0.0, 0.0], limit=5)
    assert found[0].text == "seconda versione"
