"""Le tabelle che questo agente condivide fra le proprie repliche.

Sono due cose sole, e nessuna delle due e' una conversazione: i log operativi
che il tab LOG legge, e la memoria di quali notifiche sono gia' arrivate. Vivono
in un database perche' con due repliche uno stato di processo non basta -- non
perche' un agente debba avere un database.

Numerate, idempotenti, registrate, applicate all'avvio: la stessa abitudine
degli altri servizi.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS operational_logs (
    seq         bigserial   PRIMARY KEY,
    ts          text        NOT NULL,
    level       text        NOT NULL,
    source      text        NOT NULL,
    message     text        NOT NULL,
    written_at  timestamptz NOT NULL DEFAULT now()
);

-- La finestra si legge dalla fine, e si pota dall'inizio.
CREATE INDEX IF NOT EXISTS logs_by_age ON operational_logs (written_at);

CREATE TABLE IF NOT EXISTS seen_notifications (
    key         text        PRIMARY KEY,
    seen_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS notifications_by_age ON seen_notifications (seen_at);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "log operativi e notifiche gia' viste", (SCHEMA,)),
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
    """Applica quello che manca, in ordine, e dice cosa ha applicato."""
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
