"""Schema changes that travel with the code.

Same shape as the memory service: numbered, idempotent, recorded, applied at
startup. A fork must never have to find a script and run it by hand before the
service will start.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    name        text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""

INSTANCES = """
CREATE TABLE IF NOT EXISTS process_instances (
    id                  uuid PRIMARY KEY,
    scope               text NOT NULL,
    process_id          text NOT NULL,
    process_version     integer NOT NULL,
    status              text NOT NULL,
    input               jsonb NOT NULL DEFAULT '{}'::jsonb,
    context             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS instances_by_scope_and_status
    ON process_instances (scope, status, created_at DESC);
"""

STEPS = """
CREATE TABLE IF NOT EXISTS instance_steps (
    instance_id   uuid NOT NULL REFERENCES process_instances (id) ON DELETE CASCADE,
    step_id       text NOT NULL,
    status        text NOT NULL,
    owner         text,
    output        jsonb,
    note          text,
    started_at    timestamptz,
    ended_at      timestamptz,
    PRIMARY KEY (instance_id, step_id)
);
"""


EFFECTS = """
CREATE TABLE IF NOT EXISTS side_effects (
    key          text PRIMARY KEY,
    instance_id  uuid NOT NULL REFERENCES process_instances (id) ON DELETE CASCADE,
    step_id      text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "instances and steps", (INSTANCES, STEPS)),
    Migration(2, "effects that must not repeat", (EFFECTS,)),
)

LATEST_VERSION = max(migration.version for migration in MIGRATIONS)


async def applied_versions(connection: Any) -> set[int]:
    await connection.execute(SCHEMA_TABLE)
    rows = await (await connection.execute("SELECT version FROM schema_migrations")).fetchall()
    return {int(row[0]) for row in rows}


async def run_migrations(connection: Any) -> list[Migration]:
    """Applies the missing migrations, in order, and returns what it applied."""
    already = await applied_versions(connection)
    applied: list[Migration] = []
    for migration in sorted(MIGRATIONS, key=lambda item: item.version):
        if migration.version in already:
            continue
        logger.info("Applying migration %d: %s.", migration.version, migration.name)
        for statement in migration.statements:
            await connection.execute(statement)
        await connection.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
            (migration.version, migration.name),
        )
        applied.append(migration)
    if not applied:
        logger.info("Schema up to date: %d migrations already applied.", len(already))
    return applied
