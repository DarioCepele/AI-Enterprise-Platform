"""Memoria a breve termine: la coda calda della conversazione, su Redis.

Requisiti opposti a quelli di Mongo: latenza bassa e scadenza automatica invece
di durata e storia completa. Per questo e' un'altra tecnologia e non un'altra
collezione.

**Regola che tiene in piedi il disegno:** qui non vive mai l'unica copia di un
dato. Redis puo' essere svuotato, scadere o non partire affatto, e il servizio
continua a rispondere leggendo da Mongo. Per questo il container non ha volume.
"""
from __future__ import annotations

import json
import logging

from redis.asyncio import Redis

from ..models import StoredMessage

logger = logging.getLogger(__name__)


class HotTail:
    """Ultimi messaggi di un thread, con scadenza."""

    def __init__(self, redis: Redis, ttl_seconds: int, max_messages: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._max = max_messages

    @staticmethod
    def _key(scope: str, thread_id: str) -> str:
        return f"tail:{scope}:{thread_id}"

    async def append(self, scope: str, thread_id: str, message: StoredMessage) -> None:
        """Aggiunge in coda, taglia la testa e rinnova la scadenza."""
        key = self._key(scope, thread_id)
        pipeline = self._redis.pipeline()
        pipeline.rpush(key, message.model_dump_json())
        pipeline.ltrim(key, -self._max, -1)
        pipeline.expire(key, self._ttl)
        await pipeline.execute()

    async def tail(self, scope: str, thread_id: str, limit: int) -> list[StoredMessage] | None:
        """La coda calda, o None se la cache non ce l'ha.

        Restituisce None anche quando la cache e' piena ma piu' corta di quanto
        chiesto: una risposta parziale spacciata per completa sarebbe peggio di
        una lettura in piu' su Mongo.
        """
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
        """Solleva se Redis non risponde."""
        await self._redis.ping()
