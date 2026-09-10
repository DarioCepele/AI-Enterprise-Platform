"""Durable conversation transcripts in Postgres.

One row per turn. The previous store bucketed messages into documents to avoid
paying for a document per message -- a pattern that exists to make a document
database behave like a table, and that has nothing to answer for here: a row
per message is already the cheap shape, and appending one is a single
statement instead of a find-and-push with a retry for the losing writer.

What a turn contains stays JSON: roles, payloads and provider-specific fields
vary, and that variety is real. What we query on -- scope, thread, position --
is columns, because that is what an index is for.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ..models import NewMessage, StoredMessage

logger = logging.getLogger(__name__)


def build_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    """One pool per process. Sized for a laboratory: raise both for real load."""
    return AsyncConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)


class PostgresTranscripts:
    """Threads, their turns, their summaries, and the facts of a scope."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, scope: str, thread_id: str, message: NewMessage) -> StoredMessage:
        """Append a turn and return it with the position it got.

        Position and insertion happen in **one** statement: the counter is
        bumped and the row written together, so two writers on the same thread
        cannot both take the same number, and a failure leaves neither.
        """
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """
                WITH position AS (
                    INSERT INTO threads (scope, thread_id, next_seq)
                    VALUES (%(scope)s, %(thread)s, 1)
                    ON CONFLICT (scope, thread_id) DO UPDATE
                        SET next_seq = threads.next_seq + 1,
                            updated_at = now()
                    RETURNING next_seq
                )
                INSERT INTO thread_turns (scope, thread_id, seq, message)
                SELECT %(scope)s, %(thread)s, next_seq, %(message)s FROM position
                RETURNING seq, ts
                """,
                {
                    "scope": scope,
                    "thread": thread_id,
                    "message": json.dumps(message.model_dump(), default=str),
                },
            )
            written = await cursor.fetchone()
        return StoredMessage(**message.model_dump(), seq=written["seq"], ts=written["ts"])

    async def save_head(
        self,
        scope: str,
        thread_id: str,
        *,
        state: dict[str, Any] | None,
        interrupt: list[dict[str, Any]] | None,
        session_state: dict[str, Any] | None,
    ) -> None:
        """Store the thread state that is not a message, without touching the turns."""
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO threads (scope, thread_id, state, interrupt, session_state)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (scope, thread_id) DO UPDATE
                    SET state = EXCLUDED.state,
                        interrupt = EXCLUDED.interrupt,
                        session_state = EXCLUDED.session_state,
                        updated_at = now()
                """,
                (
                    scope,
                    thread_id,
                    _json(state),
                    _json(interrupt),
                    _json(session_state),
                ),
            )

    async def read_head(self, scope: str, thread_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT state, interrupt, session_state FROM threads "
                "WHERE scope = %s AND thread_id = %s",
                (scope, thread_id),
            )
            return await cursor.fetchone()

    async def history(self, scope: str, thread_id: str) -> list[StoredMessage]:
        """The whole conversation, oldest first."""
        return await self._turns(
            "SELECT seq, ts, message FROM thread_turns "
            "WHERE scope = %s AND thread_id = %s ORDER BY seq",
            (scope, thread_id),
        )

    async def tail(self, scope: str, thread_id: str, limit: int) -> list[StoredMessage]:
        """The last `limit` turns, oldest first.

        Read backwards and turned around: what the caller wants is the end of
        the conversation, and asking the index for the last rows is cheaper
        than reading the thread to find them.
        """
        newest = await self._turns(
            "SELECT seq, ts, message FROM thread_turns "
            "WHERE scope = %s AND thread_id = %s ORDER BY seq DESC LIMIT %s",
            (scope, thread_id, limit),
        )
        return list(reversed(newest))

    async def _turns(self, query: str, parameters: tuple[Any, ...]) -> list[StoredMessage]:
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(query, parameters)
            rows = await cursor.fetchall()
        return [
            StoredMessage(**{**row["message"], "seq": row["seq"], "ts": row["ts"]})
            for row in rows
        ]

    async def save_summary(
        self,
        scope: str,
        thread_id: str,
        *,
        text: str,
        covers_to_seq: int,
        message_count: int,
        model: str,
    ) -> None:
        """A summary through `covers_to_seq`.

        Earlier summaries are kept: what the agent knew at the time is how you
        find out what compaction lost.
        """
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO thread_summaries
                    (scope, thread_id, covers_to_seq, text, message_count, model)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (scope, thread_id, covers_to_seq) DO UPDATE
                    SET text = EXCLUDED.text,
                        message_count = EXCLUDED.message_count,
                        model = EXCLUDED.model,
                        created_at = now()
                """,
                (scope, thread_id, covers_to_seq, text, message_count, model),
            )

    async def latest_summary(self, scope: str, thread_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT text, covers_to_seq FROM thread_summaries "
                "WHERE scope = %s AND thread_id = %s ORDER BY covers_to_seq DESC LIMIT 1",
                (scope, thread_id),
            )
            return await cursor.fetchone()

    async def upsert_facts(
        self, scope: str, facts: list[tuple[str, str]], *, thread_id: str
    ) -> int:
        """Write the facts of a scope, and say how many actually changed.

        The key is what makes a fact an update instead of a duplicate saying
        something slightly different.
        """
        changed = 0
        async with self._pool.connection() as connection:
            for key, value in facts:
                written = await connection.execute(
                    """
                    INSERT INTO scope_facts (scope, key, value, thread_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (scope, key) DO UPDATE
                        SET value = EXCLUDED.value,
                            thread_id = EXCLUDED.thread_id,
                            updated_at = now()
                        WHERE scope_facts.value IS DISTINCT FROM EXCLUDED.value
                    """,
                    (scope, key, value, thread_id),
                )
                changed += written.rowcount or 0
        return changed

    async def facts_of(self, scope: str, limit: int) -> list[dict[str, Any]]:
        """The most recent facts, bounded: a context that grows forever is a bill."""
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT key, value FROM scope_facts WHERE scope = %s "
                "ORDER BY updated_at DESC LIMIT %s",
                (scope, limit),
            )
            return list(await cursor.fetchall())

    async def forget_facts(self, scope: str) -> int:
        async with self._pool.connection() as connection:
            deleted = await connection.execute(
                "DELETE FROM scope_facts WHERE scope = %s", (scope,)
            )
            return deleted.rowcount or 0

    async def indexed_upto(self, scope: str, thread_id: str) -> int:
        """How far the semantic index has got. The index can disappear; this cannot."""
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT indexed_upto FROM threads WHERE scope = %s AND thread_id = %s",
                (scope, thread_id),
            )
            row = await cursor.fetchone()
        return int(row["indexed_upto"]) if row else 0

    async def set_indexed_upto(self, scope: str, thread_id: str, seq: int) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "UPDATE threads SET indexed_upto = %s WHERE scope = %s AND thread_id = %s",
                (seq, scope, thread_id),
            )

    async def seqs_of(self, scope: str, thread_id: str) -> list[int]:
        """The positions of a thread, to take it out of the index."""
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT seq FROM thread_turns WHERE scope = %s AND thread_id = %s ORDER BY seq",
                (scope, thread_id),
            )
            return [int(row["seq"]) for row in await cursor.fetchall()]

    async def threads_older_than(
        self, cutoff: datetime, scope: str | None = None
    ) -> list[tuple[str, str]]:
        """Scope and id of the threads untouched since `cutoff`, oldest first."""
        query = "SELECT scope, thread_id FROM threads WHERE updated_at < %s"
        parameters: list[Any] = [cutoff]
        if scope is not None:
            query += " AND scope = %s"
            parameters.append(scope)
        query += " ORDER BY updated_at"
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(query, parameters)
            return [(row["scope"], row["thread_id"]) for row in await cursor.fetchall()]

    async def threads_of(self, scope: str) -> list[str]:
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute("SELECT thread_id FROM threads WHERE scope = %s", (scope,))
            return [row["thread_id"] for row in await cursor.fetchall()]

    async def forget(self, scope: str, thread_id: str) -> int:
        """Delete a conversation and say how many turns went with it.

        The turns and the summaries go with the thread: the foreign key says so
        once, instead of three deletes that have to be kept in step by hand.
        """
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT count(*) AS turns FROM thread_turns "
                "WHERE scope = %s AND thread_id = %s",
                (scope, thread_id),
            )
            counted = await cursor.fetchone()
            await cursor.execute(
                "DELETE FROM threads WHERE scope = %s AND thread_id = %s", (scope, thread_id)
            )
        return int(counted["turns"]) if counted else 0

    async def ping(self) -> None:
        """Raises if the database does not answer."""
        async with self._pool.connection() as connection:
            await connection.execute("SELECT 1")


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)
