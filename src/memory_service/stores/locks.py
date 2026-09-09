"""Who is allowed to do the work, when more than one process could.

Compaction calls a model: doing it twice costs twice and the last writer wins.
A set in RAM answers that question for one process, which is the wrong scope as
soon as there are two.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300

RELEASE_IF_MINE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class InProcessLock:
    """Guards one process, and says so. The fallback when Redis is not configured."""

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


class RedisLock:
    """Guards every replica, with a lease that outlives no failure.

    The lease has a TTL, so a replica that dies mid-compaction does not keep the
    thread locked forever. It is released only by whoever took it: comparing the
    token before deleting is the difference between releasing your own lock and
    releasing the one someone else took after yours expired.
    """

    def __init__(self, redis: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(name: str) -> str:
        return f"lock:{name}"

    @asynccontextmanager
    async def hold(self, name: str):
        token = uuid4().hex
        try:
            taken = bool(await self._redis.set(self._key(name), token, nx=True, ex=self._ttl))
        except Exception:
            logger.warning(
                "Lock '%s' not taken: Redis unreachable, going on without it.",
                name,
                exc_info=True,
            )
            yield True
            return

        try:
            yield taken
        finally:
            if taken:
                await self._release(name, token)

    async def _release(self, name: str, token: str) -> None:
        try:
            released = await self._redis.eval(RELEASE_IF_MINE, 1, self._key(name), token)
        except Exception:
            logger.warning("Lock '%s' not released: it will expire.", name, exc_info=True)
            return
        if not released:
            logger.warning(
                "Lock '%s' expired before the work finished: another replica may have "
                "started the same work.",
                name,
            )
