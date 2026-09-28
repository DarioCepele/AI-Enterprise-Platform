"""The tables this agent shares between its replicas.

None of them is a conversation: the operational logs the LOG tab reads, which
push notifications already landed, and uploaded files waiting to be analyzed.
They live in a database because with two replicas a process's own memory is
half the story -- not because an agent must have a database.

The runner is the platform's (`platform_core.migrations`): numbered, recorded,
applied at startup, one replica at a time.
"""

from __future__ import annotations

from platform_core.migrations import Migration, applied_versions
from platform_core.migrations import run_migrations as _run
from psycopg import AsyncConnection

__all__ = [
    "LATEST_VERSION",
    "MIGRATIONS",
    "Migration",
    "applied_versions",
    "run_migrations",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS operational_logs (
    seq         bigserial   PRIMARY KEY,
    ts          text        NOT NULL,
    level       text        NOT NULL,
    source      text        NOT NULL,
    message     text        NOT NULL,
    written_at  timestamptz NOT NULL DEFAULT now()
);

-- The window is read from the end and pruned from the start.
CREATE INDEX IF NOT EXISTS logs_by_age ON operational_logs (written_at);

CREATE TABLE IF NOT EXISTS seen_notifications (
    key         text        PRIMARY KEY,
    seen_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS notifications_by_age ON seen_notifications (seen_at);
"""

UPLOADS = """
CREATE TABLE IF NOT EXISTS uploads (
    id            text        PRIMARY KEY,
    content_type  text        NOT NULL,
    size          bigint      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Expiry reads it: which uploads nobody may fetch any more.
CREATE INDEX IF NOT EXISTS uploads_by_age ON uploads (created_at);

CREATE TABLE IF NOT EXISTS upload_chunks (
    upload_id   text     NOT NULL REFERENCES uploads (id) ON DELETE CASCADE,
    seq         integer  NOT NULL,
    data        bytea    NOT NULL,
    PRIMARY KEY (upload_id, seq)
);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "operational logs and notifications already seen", (SCHEMA,)),
    Migration(2, "uploads every replica can read", (UPLOADS,)),
)

LATEST_VERSION = max(migration.version for migration in MIGRATIONS)

LOCK = "master-agent"


async def run_migrations(connection: AsyncConnection) -> list[Migration]:
    """Applies what is missing, in order, one replica at a time."""
    return await _run(connection, MIGRATIONS, lock=LOCK)
