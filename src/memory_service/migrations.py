"""Schema changes that travel with the code.

Numbered, idempotent, recorded, and applied at startup: a fork should never
have to find a script and run it by hand before the service will start. That
lesson was paid for once, when renaming a field left an index that could not be
built and a service that would not boot.

The same shape as the process service's migrations, on purpose: two services,
one habit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    scope           text        NOT NULL,
    thread_id       text        NOT NULL,
    next_seq        bigint      NOT NULL DEFAULT 0,
    state           jsonb,
    interrupt       jsonb,
    session_state   jsonb,
    indexed_upto    bigint      NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, thread_id)
);

-- Retention reads it: which threads nobody has touched since a date.
CREATE INDEX IF NOT EXISTS threads_by_age ON threads (updated_at);

CREATE TABLE IF NOT EXISTS thread_turns (
    scope       text        NOT NULL,
    thread_id   text        NOT NULL,
    seq         bigint      NOT NULL,
    message     jsonb       NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, thread_id, seq),
    FOREIGN KEY (scope, thread_id) REFERENCES threads (scope, thread_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS thread_summaries (
    scope           text        NOT NULL,
    thread_id       text        NOT NULL,
    covers_to_seq   bigint      NOT NULL,
    text            text        NOT NULL,
    message_count   integer     NOT NULL,
    model           text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, thread_id, covers_to_seq),
    FOREIGN KEY (scope, thread_id) REFERENCES threads (scope, thread_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scope_facts (
    scope       text        NOT NULL,
    key         text        NOT NULL,
    value       text        NOT NULL,
    thread_id   text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key)
);

-- What a context is filled with: the most recent facts of a scope.
CREATE INDEX IF NOT EXISTS facts_by_age ON scope_facts (scope, updated_at DESC);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "threads, turns, summaries and facts", (SCHEMA,)),
)

LATEST_VERSION = max(migration.version for migration in MIGRATIONS)

REGISTER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer     PRIMARY KEY,
    name        text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


async def applied_versions(connection: AsyncConnection) -> set[int]:
    await connection.execute(REGISTER)
    rows = await (await connection.execute("SELECT version FROM schema_migrations")).fetchall()
    return {int(row[0]) for row in rows}


async def run_migrations(connection: AsyncConnection) -> list[Migration]:
    """Applies what is missing, in order, and says what it applied."""
    already = await applied_versions(connection)
    applied: list[Migration] = []
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in already:
            continue
        logger.info("Applying migration %d: %s.", migration.version, migration.name)
        for statement in migration.statements:
            await connection.execute(statement)
        await connection.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (%s, %s) "
            "ON CONFLICT (version) DO NOTHING",
            (migration.version, migration.name),
        )
        applied.append(migration)
    if not applied:
        logger.info("Schema up to date: %d migrations already applied.", len(already))
    return applied


async def missing_migrations(connection: AsyncConnection) -> list[int]:
    return sorted({migration.version for migration in MIGRATIONS} - await applied_versions(connection))
