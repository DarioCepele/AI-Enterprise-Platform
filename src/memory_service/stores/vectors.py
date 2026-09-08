"""Ricordi cercabili per significato, sui vector set di Redis 8.

Perche' qui e non su Mongo: `$vectorSearch` esiste solo su Atlas, mentre da
Redis 8 il Query Engine -- vector set compresi -- sta nella distribuzione open
source. Redis c'e' gia' per la coda calda, quindi la ricerca per significato
non aggiunge un terzo datastore.

Vale la regola di sempre: qui non vive l'unica copia di nulla. L'indice si
ricostruisce dai transcript su Mongo; perderlo costa una reindicizzazione, non
una conversazione.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Memory:
    """Un ricordo trovato: da dove viene, cosa diceva, quanto somiglia."""

    thread_id: str
    seq: int
    testo: str
    somiglianza: float


class RedisMemories:
    """Un vector set per scope. Lo scope e' nella chiave, non nel filtro."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(scope: str) -> str:
        return f"ricordi:{scope}"

    @staticmethod
    def _element(thread_id: str, seq: int) -> str:
        return f"{thread_id}:{seq}"

    async def index(
        self,
        scope: str,
        entries: list[tuple[str, int, str]],
        vectors: list[list[float]],
    ) -> int:
        """Aggiunge ricordi all'indice. `entries` e' (thread_id, seq, testo)."""
        added = 0
        for (thread_id, seq, testo), vector in zip(entries, vectors, strict=True):
            attributes = json.dumps(
                {"thread_id": thread_id, "seq": seq, "testo": testo}, ensure_ascii=False
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
        """I ricordi piu' vicini al vettore della domanda."""
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
                    testo=str(data.get("testo", "")),
                    somiglianza=float(score),
                )
            )
        return found

    async def forget_thread(self, scope: str, thread_id: str, seqs: list[int]) -> int:
        """Toglie dall'indice i ricordi di un thread cancellato.

        Serve la lista delle posizioni perche' un vector set non si scandisce
        per prefisso: chi cancella il thread le ha appena lette dal durevole.
        """
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
