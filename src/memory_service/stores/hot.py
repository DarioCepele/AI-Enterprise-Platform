"""Short-term conversation tails in Redis, optimized for low latency and automatic expiry. Redis never holds the only copy: Mongo remains readable when the cache expires, is cleared, or is unavailable. The cache container therefore needs no volume."""
from __future__ import annotations

import json
import logging

from redis.asyncio import Redis

from ..models import StoredMessage

logger = logging.getLogger(__name__)


class HotTail:
    """Expiring recent messages for a thread."""

    def __init__(self, redis: Redis, ttl_seconds: int, max_messages: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._max = max_messages

    @staticmethod
    def _key(scope: str, thread_id: str) -> str:
        return f"tail:{scope}:{thread_id}"

    async def append(self, scope: str, thread_id: str, message: StoredMessage) -> None:
        """Append messages, trim the head, and renew expiry."""
        key = self._key(scope, thread_id)
        pipeline = self._redis.pipeline()
        pipeline.rpush(key, message.model_dump_json())
        pipeline.ltrim(key, -self._max, -1)
        pipeline.expire(key, self._ttl)
        await pipeline.execute()

    async def tail(self, scope: str, thread_id: str, limit: int) -> list[StoredMessage] | None:
        """Return the cached tail or None. An incomplete cache also returns None so callers can fetch the complete result from Mongo."""
        key = self._key(scope, thread_id)
        cached = await self._redis.lrange(key, -limit, -1)
        if not cached:
            return None
        if len(cached) < limit and await self._redis.llen(key) < limit:
            return None
        return [StoredMessage(**json.loads(entry)) for entry in cached]

    async def forget(self, scope: str, thread_id: str) -> None:
        await self._redis.delete(self._key(scope, thread_id))

    async def ping(self) -> None:
        """Raise if Redis does not respond."""
        await self._redis.ping()
