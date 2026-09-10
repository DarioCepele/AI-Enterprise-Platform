"""Schema changes travel with the code, not with a script somebody has to find."""
from __future__ import annotations

import pytest

from memory_service.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
    applied_versions,
    missing_migrations,
    run_migrations,
)
from memory_service.models import NewMessage
from memory_service.stores.postgres import PostgresTranscripts

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]


async def test_the_versions_applied_are_recorded(pool):
    async with pool.connection() as connection:
        assert await applied_versions(connection) == {m.version for m in MIGRATIONS}
        assert await missing_migrations(connection) == []


async def test_applying_twice_changes_nothing_the_second_time(pool):
    async with pool.connection() as connection:
        before = await applied_versions(connection)

        applied = await run_migrations(connection)

        assert applied == []
        assert await applied_versions(connection) == before


async def test_the_schema_is_what_the_store_writes_into(pool, scope):
    """The migration and the store are one thing: if they drift, this fails.

    The alternative is finding out on the first append, in front of whoever is
    using the service.
    """
    store = PostgresTranscripts(pool)

    stored = await store.append(scope, "t1", NewMessage(role="user", content="ciao"))
    await store.save_summary(
        scope, "t1", text="a summary", covers_to_seq=1, message_count=1, model="m"
    )
    await store.upsert_facts(scope, [("contact", "Marta")], thread_id="t1")

    assert stored.seq == 1
    assert (await store.latest_summary(scope, "t1"))["covers_to_seq"] == 1
    assert await store.facts_of(scope, limit=10) == [{"key": "contact", "value": "Marta"}]


async def test_a_thread_takes_its_turns_and_summaries_with_it(pool, scope):
    """The foreign key says it once, instead of three deletes kept in step by hand."""
    store = PostgresTranscripts(pool)
    await store.append(scope, "t1", NewMessage(role="user", content="ciao"))
    await store.save_summary(
        scope, "t1", text="a summary", covers_to_seq=1, message_count=1, model="m"
    )

    await store.forget(scope, "t1")

    async with pool.connection() as connection:
        turns = await (
            await connection.execute(
                "SELECT count(*) FROM thread_turns WHERE scope = %s", (scope,)
            )
        ).fetchone()
        summaries = await (
            await connection.execute(
                "SELECT count(*) FROM thread_summaries WHERE scope = %s", (scope,)
            )
        ).fetchone()

    assert turns[0] == 0
    assert summaries[0] == 0


def test_the_latest_version_is_the_highest_one():
    assert LATEST_VERSION == max(m.version for m in MIGRATIONS)
