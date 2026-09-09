"""Schema changes travel with the code, not with a script somebody has to find."""
from __future__ import annotations

import pytest
from pymongo import ASCENDING

from memory_service.migrations import MIGRATIONS, applied_versions, run_migrations
from memory_service.stores.mongo import FACTS, TURNS, MongoTranscripts

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]


@pytest.fixture
async def database(mongo_client, scope):
    name = f"migrations_{scope.replace('-', '_')}"
    try:
        yield mongo_client[name]
    finally:
        await mongo_client.drop_database(name)


async def test_the_versions_applied_are_recorded(database):
    await run_migrations(database)

    assert await applied_versions(database) == {m.version for m in MIGRATIONS}


async def test_applying_twice_changes_nothing_the_second_time(database, caplog):
    await run_migrations(database)
    before = await applied_versions(database)

    applied = await run_migrations(database)

    assert applied == []
    assert await applied_versions(database) == before


async def test_the_italian_fields_of_the_facts_become_english(database):
    await database[FACTS].insert_one({"scope": "s", "chiave": "contact", "valore": "Marta"})

    await run_migrations(database)

    fact = await database[FACTS].find_one({"scope": "s"})
    assert fact["key"] == "contact"
    assert fact["value"] == "Marta"
    assert "chiave" not in fact


async def test_documents_already_migrated_are_left_alone(database):
    await database[FACTS].insert_one({"scope": "s", "key": "contact", "value": "Giulio"})

    await run_migrations(database)

    assert (await database[FACTS].find_one({"scope": "s"}))["value"] == "Giulio"


async def test_the_indexes_of_the_old_names_are_removed(database):
    await database[TURNS].create_index(
        [("scope", ASCENDING), ("thread_id", ASCENDING), ("bucket", ASCENDING)],
        unique=True,
        name="thread_bucket_unico",
    )

    await run_migrations(database)

    names = {index["name"] for index in await (await database[TURNS].list_indexes()).to_list()}
    assert "thread_bucket_unico" not in names


async def test_the_indexes_are_created_after_the_migrations(database):
    # An index unique on a field the old documents do not have would admit one
    # document per scope: the order between the two is the whole point.
    await database[FACTS].insert_many(
        [
            {"scope": "s", "chiave": "one", "valore": "1"},
            {"scope": "s", "chiave": "two", "valore": "2"},
        ]
    )

    await run_migrations(database)
    await MongoTranscripts(database.client, database.name, bucket_size=3).ensure_indexes()

    assert await database[FACTS].count_documents({"scope": "s"}) == 2


async def test_indexes_without_the_migrations_say_which_migration_is_missing(database):
    await database[FACTS].insert_many(
        [
            {"scope": "s", "chiave": "one", "valore": "1"},
            {"scope": "s", "chiave": "two", "valore": "2"},
        ]
    )
    store = MongoTranscripts(database.client, database.name, bucket_size=3)

    with pytest.raises(RuntimeError, match="run_migrations"):
        await store.ensure_indexes()
