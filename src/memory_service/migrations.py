"""Schema changes that travel with the code.

Renaming the fields of the durable facts once took a one-off script, and until
someone ran it the service would not start: the unique index could not be built
on a field the stored documents did not have. A fork should never inherit that.

Every migration is idempotent, is recorded by version, and runs before the
indexes are created -- an index over a field the old documents lack is exactly
what fails when the order is wrong.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

MIGRATIONS_COLLECTION = "schema_migrations"

FACTS = "scope_facts"
TURNS = "thread_turns"
THREADS = "threads"
SUMMARIES = "thread_summaries"

LEGACY_INDEX_NAMES = {
    TURNS: ("thread_bucket_unico", "coda_del_thread"),
    THREADS: ("thread_unico",),
    FACTS: ("fatto_unico",),
    SUMMARIES: ("riassunto_unico",),
}


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Any], Awaitable[None]]


async def _english_fact_fields(db: Any) -> None:
    """`chiave`/`valore` become `key`/`value`.

    Renaming instead of deleting: those facts were learned from real
    conversations, and a rename keeps them readable under the new names.
    """
    result = await db[FACTS].update_many(
        {"chiave": {"$exists": True}}, {"$rename": {"chiave": "key", "valore": "value"}}
    )
    if result.modified_count:
        logger.info("Migration 1: %d facts renamed to key/value.", result.modified_count)


async def _english_index_names(db: Any) -> None:
    """Drops the indexes built under the old names.

    They are not merely untidy: a unique index over `chiave`, a field no
    document has any more, would admit a single document per scope.
    """
    for collection, names in LEGACY_INDEX_NAMES.items():
        existing = {index["name"] for index in await (await db[collection].list_indexes()).to_list()}
        for name in names:
            if name in existing:
                await db[collection].drop_index(name)
                logger.info("Migration 2: index %s.%s removed.", collection, name)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "english fact fields", _english_fact_fields),
    Migration(2, "english index names", _english_index_names),
)

LATEST_VERSION = max(migration.version for migration in MIGRATIONS)


async def applied_versions(db: Any) -> set[int]:
    documents = await db[MIGRATIONS_COLLECTION].find({}, projection={"_id": 1}).to_list(None)
    return {int(document["_id"]) for document in documents}


async def run_migrations(db: Any) -> list[Migration]:
    """Applies the missing migrations, in order, and returns what it applied."""
    already = await applied_versions(db)
    applied: list[Migration] = []
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in already:
            continue
        logger.info("Applying migration %d: %s.", migration.version, migration.name)
        await migration.apply(db)
        await db[MIGRATIONS_COLLECTION].update_one(
            {"_id": migration.version},
            {"$set": {"name": migration.name, "applied_at": datetime.now(UTC)}},
            upsert=True,
        )
        applied.append(migration)
    if not applied:
        logger.info("Schema up to date: %d migrations already applied.", len(already))
    return applied


async def missing_migrations(db: Any) -> list[int]:
    return sorted({migration.version for migration in MIGRATIONS} - await applied_versions(db))
