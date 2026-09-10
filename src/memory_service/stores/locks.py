"""Who is allowed to do the work, when more than one process could.

Compaction calls a model: doing it twice costs twice and the last writer wins.
A set in RAM answers that question for one process, which is the wrong scope as
soon as there are two.
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class InProcessLock:
    """Guards one process, and says so. The fallback when there is no database."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    @asynccontextmanager
    async def hold(self, name: str):
        if name in self._held:
            yield False
            return
        self._held.add(name)
        try:
            yield True
        finally:
            self._held.discard(name)


class PostgresLock:
    """Guards every replica, with an advisory lock.

    There is no lease to expire and no token to compare: the lock lives on the
    connection that took it, so a replica that dies loses it the moment its
    connection goes. The Redis version needed a TTL for that -- long enough to
    outlast the work, short enough to unstick a dead replica -- and a Lua script
    to make sure it deleted its own lock and not the one somebody took after its
    lease ran out. Postgres answers both with the same primitive.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _key(name: str) -> int:
        """A name into the 64-bit number an advisory lock is addressed by."""
        digest = hashlib.blake2b(name.encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big", signed=True)

    @asynccontextmanager
    async def hold(self, name: str):
        try:
            connection_manager = self._pool.connection()
            connection = await connection_manager.__aenter__()
        except Exception:
            logger.warning(
                "Lock '%s' not taken: the database is unreachable, going on without it.",
                name,
                exc_info=True,
            )
            yield True
            return

        key = self._key(name)
        try:
            taken = bool(
                (await (await connection.execute("SELECT pg_try_advisory_lock(%s)", (key,))).fetchone())[0]
            )
            try:
                yield taken
            finally:
                if taken:
                    await connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
        finally:
            # The connection goes back to the pool only here: holding the lock
            # means holding the connection that took it.
            await connection_manager.__aexit__(None, None, None)
