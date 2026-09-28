"""Schema changes that travel with the code, applied at startup, one replica at a time.

Numbered, idempotent, recorded in `schema_migrations`, applied in order: a fork
never has to find a script and run it by hand before a service will start.

Two replicas start together more often than not -- a rollout does exactly
that -- and two `CREATE TABLE IF NOT EXISTS` racing each other can still fail
on the catalogue. A transaction-scoped advisory lock serialises them: the
second waits, then finds everything already applied. Being transaction-scoped,
the lock goes away with the transaction, commit or rollback, and cannot be
left behind on a pooled connection.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


class _Cursor(Protocol):
    async def fetchall(self) -> list[Any]: ...


class Connection(Protocol):
    """The part of psycopg's AsyncConnection this module uses."""

    async def execute(self, query: Any, params: Any = ...) -> Any: ...


REGISTER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer     PRIMARY KEY,
    name        text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def lock_key(name: str) -> int:
    """A name into the signed 64-bit number an advisory lock is addressed by."""
    digest = hashlib.blake2b(name.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def applied_versions(connection: Connection) -> set[int]:
    await connection.execute(REGISTER)
    cursor = await connection.execute("SELECT version FROM schema_migrations")
    rows = await cursor.fetchall()
    return {int(row[0]) for row in rows}


async def run_migrations(
    connection: Connection, migrations: Sequence[Migration], *, lock: str
) -> list[Migration]:
    """Applies what is missing, in order, and says what it applied.

    `connection` must be inside a transaction (psycopg's default, and what a
    pool connection context gives): the advisory lock lives exactly as long
    as that transaction.
    """
    await connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(lock),))
    already = await applied_versions(connection)
    applied: list[Migration] = []
    for migration in sorted(migrations, key=lambda m: m.version):
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


async def missing_migrations(
    connection: Connection, migrations: Sequence[Migration]
) -> list[int]:
    known = {migration.version for migration in migrations}
    return sorted(known - await applied_versions(connection))
