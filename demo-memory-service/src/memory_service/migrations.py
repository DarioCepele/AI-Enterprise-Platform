"""Schema changes that travel with the code.

Numbered, idempotent, recorded, and applied at startup: a fork should never
have to find a script and run it by hand before the service will start. That
lesson was paid for once, when renaming a field left an index that could not be
built and a service that would not boot.

The runner is the platform's (`platform_core.migrations`): numbered, recorded,
and serialised by an advisory lock, so two replicas starting together apply
each migration once.
"""

from __future__ import annotations

import logging

from platform_core.migrations import Migration, applied_versions
from platform_core.migrations import missing_migrations as _missing
from platform_core.migrations import run_migrations as _run
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
    FOREIGN KEY (scope, thread_id)
        REFERENCES threads (scope, thread_id) ON DELETE CASCADE
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
    FOREIGN KEY (scope, thread_id)
        REFERENCES threads (scope, thread_id) ON DELETE CASCADE
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


MEMORIES = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    scope       text    NOT NULL,
    thread_id   text    NOT NULL,
    seq         bigint  NOT NULL,
    text        text    NOT NULL,
    embedding   vector  NOT NULL,
    PRIMARY KEY (scope, thread_id, seq)
);
"""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "threads, turns, summaries and facts", (SCHEMA,)),
    # No index on the embeddings: exact search is right below the tens of
    # thousands of vectors, and an HNSW index built too early costs memory and
    # accuracy for a scan that takes a millisecond. The moment a scope grows,
    # an HNSW index is one migration away -- on an expression with a cast,
    # `USING hnsw ((embedding::vector(1536)) vector_cosine_ops)`, because the
    # column does not fix a dimension (pgvector's documented pattern), and the
    # query has to use the same cast to be served by it.
    Migration(2, "the index of the memories", (MEMORIES,)),
)

LATEST_VERSION = max(migration.version for migration in MIGRATIONS)

__all__ = [
    "LATEST_VERSION",
    "MIGRATIONS",
    "Migration",
    "applied_versions",
    "missing_migrations",
    "run_migrations",
]

LOCK = "memory-service"


async def run_migrations(connection: AsyncConnection) -> list[Migration]:
    """Applies what is missing, in order, one replica at a time."""
    return await _run(connection, MIGRATIONS, lock=LOCK)


async def missing_migrations(connection: AsyncConnection) -> list[int]:
    return await _missing(connection, MIGRATIONS)
