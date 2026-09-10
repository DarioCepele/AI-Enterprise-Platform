"""Semantic memory search using Redis 8 vector sets. Redis already serves the hot cache, avoiding another datastore. The index is reconstructible from the transcripts in Postgres; losing it requires reindexing rather than losing conversations."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Memory:
    """A retrieved memory with its source, text, and similarity score."""

    thread_id: str
    seq: int
    text: str
    similarity: float


class RedisMemories:
    """Use one vector set per scope; scope isolation is encoded in the key."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(scope: str) -> str:
        return f"memories:{scope}"

    @staticmethod
    def _element(thread_id: str, seq: int) -> str:
        return f"{thread_id}:{seq}"

    async def index(
        self,
        scope: str,
        entries: list[tuple[str, int, str]],
        vectors: list[list[float]],
    ) -> int:
        """Index entries containing thread_id, seq, and text."""
        added = 0
        for (thread_id, seq, text), vector in zip(entries, vectors, strict=True):
            attributes = json.dumps(
                {"thread_id": thread_id, "seq": seq, "text": text}, ensure_ascii=False
            )
            await self._redis.execute_command(
                "VADD",
                self._key(scope),
                "VALUES",
                len(vector),
                *[repr(value) for value in vector],
                self._element(thread_id, seq),
                "SETATTR",
                attributes,
            )
            added += 1
        return added

    async def search(self, scope: str, vector: list[float], limit: int) -> list[Memory]:
        """Find memories closest to the query vector."""
        raw: list[Any] = await self._redis.execute_command(
            "VSIM",
            self._key(scope),
            "VALUES",
            len(vector),
            *[repr(value) for value in vector],
            "WITHSCORES",
            "COUNT",
            limit,
        )
        if not raw:
            return []

        pairs = raw.items() if isinstance(raw, dict) else zip(raw[0::2], raw[1::2], strict=False)

        found: list[Memory] = []
        for element, score in pairs:
            attributes = await self._redis.execute_command(
                "VGETATTR", self._key(scope), element
            )
            if not attributes:
                continue
            data = json.loads(attributes)
            found.append(
                Memory(
                    thread_id=str(data.get("thread_id", "")),
                    seq=int(data.get("seq", 0)),
                    text=str(data.get("text", "")),
                    similarity=float(score),
                )
            )
        return found

    async def forget_thread(self, scope: str, thread_id: str, seqs: list[int]) -> int:
        """Remove a deleted thread from the index using positions read from durable storage; vector sets cannot be scanned by prefix."""
        removed = 0
        for seq in seqs:
            gone = await self._redis.execute_command(
                "VREM", self._key(scope), self._element(thread_id, seq)
            )
            removed += int(gone or 0)
        return removed

    async def forget_scope(self, scope: str) -> None:
        await self._redis.delete(self._key(scope))

    async def count(self, scope: str) -> int:
        return int(await self._redis.execute_command("VCARD", self._key(scope)) or 0)
