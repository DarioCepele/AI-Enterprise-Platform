"""Semantic search over the memories, with pgvector.

The index now sits next to the transcripts it is rebuilt from, in the same
database: losing it is still a reindex and not a loss, but there is one system
left to run instead of two. pgvector is the ordinary answer when vector search
is **one feature among many** -- it holds to the low tens of millions of
vectors per node, and this laboratory is five orders of magnitude below that.

A dedicated engine becomes the right answer when somebody can name the
bottleneck: past ~10M vectors, or when the search is the dominant workload
rather than a feature of it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Memory:
    """A retrieved memory with its source, text, and similarity score."""

    thread_id: str
    seq: int
    text: str
    similarity: float


class PostgresMemories:
    """One row per indexed turn, and cosine distance to find them again."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def index(
        self,
        scope: str,
        entries: list[tuple[str, int, str]],
        vectors: list[list[float]],
    ) -> int:
        """Index entries containing thread_id, seq, and text."""
        added = 0
        async with self._pool.connection() as connection:
            for (thread_id, seq, text), vector in zip(entries, vectors, strict=True):
                await connection.execute(
                    """
                    INSERT INTO memories (scope, thread_id, seq, text, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (scope, thread_id, seq) DO UPDATE
                        SET text = EXCLUDED.text,
                            embedding = EXCLUDED.embedding
                    """,
                    (scope, thread_id, seq, text, _vector(vector)),
                )
                added += 1
        return added

    async def search(self, scope: str, vector: list[float], limit: int) -> list[Memory]:
        """The memories closest to the query, nearest first.

        `<=>` is cosine distance: zero is identical, so the similarity handed
        back is one minus it -- the number the rest of the service already
        speaks in.
        """
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """
                SELECT thread_id, seq, text, 1 - (embedding <=> %s::vector) AS similarity
                FROM memories
                WHERE scope = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (_vector(vector), scope, _vector(vector), limit),
            )
            rows = await cursor.fetchall()
        return [
            Memory(
                thread_id=row["thread_id"],
                seq=int(row["seq"]),
                text=row["text"],
                similarity=float(row["similarity"]),
            )
            for row in rows
        ]

    async def forget_thread(self, scope: str, thread_id: str, seqs: list[int]) -> int:
        """Take a deleted thread out of the index.

        The positions still arrive from the caller, because that is what the
        durable store knows; here a `DELETE ... WHERE thread_id` would do as
        well, and the signature stays so the caller does not have to care which
        index is underneath.
        """
        async with self._pool.connection() as connection:
            removed = await connection.execute(
                "DELETE FROM memories WHERE scope = %s AND thread_id = %s AND seq = ANY(%s)",
                (scope, thread_id, list(seqs)),
            )
            return removed.rowcount or 0

    async def forget_scope(self, scope: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute("DELETE FROM memories WHERE scope = %s", (scope,))

    async def count(self, scope: str) -> int:
        async with self._pool.connection() as connection:
            counted = await (
                await connection.execute(
                    "SELECT count(*) FROM memories WHERE scope = %s", (scope,)
                )
            ).fetchone()
        return int(counted[0]) if counted else 0


def _vector(values: list[float]) -> str:
    """pgvector reads a vector as its text form: `[0.1,0.2,...]`."""
    return json.dumps([float(value) for value in values])
