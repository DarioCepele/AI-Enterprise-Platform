"""Memoria durevole: i transcript delle conversazioni su MongoDB.

Questo e' l'unico posto dove un turno esiste davvero. Redis tiene una copia
calda, e ogni cosa che sta in Redis deve poter essere ricostruita da qui.

**Vincolo del deployment, non preferenza.** L'istanza e' un nodo singolo senza
replica set: niente transazioni multi-documento. Quindi ogni scrittura che deve
essere atomica sta dentro **un solo documento**, e il codice non usa mai una
sequenza di scritture che, interrotta a meta', lascerebbe uno stato illegale.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..models import NewMessage, StoredMessage

logger = logging.getLogger(__name__)

TURNS = "thread_turns"
THREADS = "threads"
SUMMARIES = "thread_summaries"
FACTS = "scope_facts"


def build_client(uri: str) -> AsyncMongoClient:
    """Il client Mongo del processo. Uno solo, condiviso, mai per richiesta.

    Aprire una connessione costa: TCP, TLS e autenticazione. I valori sotto
    valgono per **questo** profilo -- un container di laboratorio, un'istanza
    sola dell'applicazione, operazioni brevi, concorrenza di poche richieste --
    e vanno rialzati se il servizio viene replicato o messo sotto carico vero.
    """
    return AsyncMongoClient(
        uri,
        maxPoolSize=20,
        minPoolSize=2,
        maxIdleTimeMS=300_000,
        connectTimeoutMS=5_000,
        serverSelectionTimeoutMS=5_000,
        socketTimeoutMS=30_000,
        tz_aware=True,
    )


class MongoTranscripts:
    """I transcript, in documenti bucket da `bucket_size` messaggi l'uno."""

    def __init__(self, client: AsyncMongoClient, database: str, bucket_size: int) -> None:
        self._db = client[database]
        self._bucket_size = bucket_size

    async def ensure_indexes(self) -> None:
        """Indici idempotenti, creati all'avvio.

        L'unicita' di (scope, thread, bucket) non e' un dettaglio: e' cio' che
        rende sicura la creazione di un bucket nuovo senza transazioni. Due
        scritture in corsa sullo stesso bucket nuovo non possono riuscire
        entrambe, e la perdente ritenta.
        """
        await self._db[TURNS].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING), ("bucket", ASCENDING)],
            unique=True,
            name="thread_bucket_unico",
        )
        await self._db[TURNS].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING), ("last_seq", DESCENDING)],
            name="coda_del_thread",
        )
        await self._db[THREADS].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING)],
            unique=True,
            name="thread_unico",
        )
        await self._db[FACTS].create_index(
            [("scope", ASCENDING), ("chiave", ASCENDING)],
            unique=True,
            name="fatto_unico",
        )
        await self._db[SUMMARIES].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING), ("covers_to_seq", DESCENDING)],
            unique=True,
            name="riassunto_unico",
        )

    async def _next_seq(self, scope: str, thread_id: str) -> int:
        """Numero di posizione del prossimo messaggio.

        `$inc` su un solo documento e' atomico anche senza transazioni. Se la
        scrittura del messaggio poi fallisce, resta un numero saltato: una
        lacuna nella numerazione, non un messaggio perso o duplicato.
        """
        now = datetime.now(UTC)
        document = await self._db[THREADS].find_one_and_update(
            {"scope": scope, "thread_id": thread_id},
            {
                "$inc": {"next_seq": 1},
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(document["next_seq"])

    async def append(self, scope: str, thread_id: str, message: NewMessage) -> StoredMessage:
        """Aggiunge un turno in coda e restituisce come e' stato scritto."""
        seq = await self._next_seq(scope, thread_id)
        stored = StoredMessage(
            **message.model_dump(),
            seq=seq,
            ts=datetime.now(UTC),
        )
        entry = stored.model_dump()

        updated = await self._db[TURNS].find_one_and_update(
            {
                "scope": scope,
                "thread_id": thread_id,
                "count": {"$lt": self._bucket_size},
            },
            {
                "$push": {"messages": entry},
                "$inc": {"count": 1},
                "$set": {"last_seq": seq, "updated_at": stored.ts},
            },
            sort=[("bucket", DESCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return stored

        last = await self._db[TURNS].find_one(
            {"scope": scope, "thread_id": thread_id},
            sort=[("bucket", DESCENDING)],
            projection={"bucket": 1},
        )
        bucket = 0 if last is None else int(last["bucket"]) + 1
        try:
            await self._db[TURNS].insert_one(
                {
                    "scope": scope,
                    "thread_id": thread_id,
                    "bucket": bucket,
                    "count": 1,
                    "first_seq": seq,
                    "last_seq": seq,
                    "created_at": stored.ts,
                    "updated_at": stored.ts,
                    "messages": [entry],
                }
            )
        except DuplicateKeyError:
            logger.info("Bucket %d gia' creato da un'altra scrittura, ritento in coda.", bucket)
            await self._db[TURNS].update_one(
                {"scope": scope, "thread_id": thread_id, "bucket": bucket},
                {
                    "$push": {"messages": entry},
                    "$inc": {"count": 1},
                    "$set": {"last_seq": seq, "updated_at": stored.ts},
                },
            )
        return stored

    async def save_head(
        self,
        scope: str,
        thread_id: str,
        *,
        state: dict[str, Any] | None,
        interrupt: list[dict[str, Any]] | None,
        session_state: dict[str, Any] | None,
    ) -> None:
        """Lo stato del thread che non sono i messaggi.

        Sta nel documento contatore, non nei bucket: e' un valore solo, sempre
        l'ultimo, e riscriverlo non deve toccare la conversazione.
        """
        await self._db[THREADS].update_one(
            {"scope": scope, "thread_id": thread_id},
            {
                "$set": {
                    "state": state,
                    "interrupt": interrupt,
                    "session_state": session_state,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC), "next_seq": 0},
            },
            upsert=True,
        )

    async def read_head(self, scope: str, thread_id: str) -> dict[str, Any] | None:
        return await self._db[THREADS].find_one(
            {"scope": scope, "thread_id": thread_id},
            projection={"state": 1, "interrupt": 1, "session_state": 1, "_id": 0},
        )

    async def history(self, scope: str, thread_id: str) -> list[StoredMessage]:
        """Tutta la conversazione, dal primo turno all'ultimo.

        La usa la ricostruzione dello snapshot, che deve restituire il thread
        intero. Quando arrivera' la compattazione, sara' questa a diventare
        "riassunto piu' coda" -- ed e' il motivo per cui e' una funzione a se'
        e non una `tail` con un limite grande.
        """
        messages: list[StoredMessage] = []
        cursor = (
            self._db[TURNS]
            .find({"scope": scope, "thread_id": thread_id}, projection={"messages": 1})
            .sort("bucket", ASCENDING)
        )
        async for document in cursor:
            messages.extend(StoredMessage(**entry) for entry in document.get("messages", []))
        return messages

    async def tail(self, scope: str, thread_id: str, limit: int) -> list[StoredMessage]:
        """Gli ultimi `limit` messaggi, dal piu' vecchio al piu' recente.

        Si leggono i bucket dal fondo e ci si ferma appena bastano: una coda di
        venti messaggi non deve leggere una conversazione di mille.
        """
        collected: list[dict[str, Any]] = []
        cursor = (
            self._db[TURNS]
            .find({"scope": scope, "thread_id": thread_id}, projection={"messages": 1})
            .sort("bucket", DESCENDING)
        )
        async for document in cursor:
            collected = list(document.get("messages", [])) + collected
            if len(collected) >= limit:
                break
        return [StoredMessage(**entry) for entry in collected[-limit:]]

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
        """Registra un riassunto dei turni fino a `covers_to_seq`.

        I riassunti si accumulano invece di sostituirsi: quello vecchio dice
        cosa sapeva l'agente allora, e quando un riassunto perde qualcosa e'
        l'unico modo di risalire a dove si e' perso.
        """
        await self._db[SUMMARIES].update_one(
            {"scope": scope, "thread_id": thread_id, "covers_to_seq": covers_to_seq},
            {
                "$set": {
                    "text": text,
                    "message_count": message_count,
                    "model": model,
                    "created_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def latest_summary(self, scope: str, thread_id: str) -> dict[str, Any] | None:
        """Il riassunto piu' avanzato del thread, se ce n'e' uno."""
        return await self._db[SUMMARIES].find_one(
            {"scope": scope, "thread_id": thread_id},
            sort=[("covers_to_seq", DESCENDING)],
            projection={"text": 1, "covers_to_seq": 1, "_id": 0},
        )

    async def upsert_facts(
        self,
        scope: str,
        facts: list[tuple[str, str]],
        *,
        thread_id: str,
    ) -> int:
        """Scrive i fatti dello scope. Restituisce quanti ne sono cambiati.

        Chiave sola per scope: lo stesso fatto ridetto **aggiorna** invece di
        duplicare. Un agente che crede due valori diversi della stessa cosa e'
        peggio di uno che non la sa.
        """
        changed = 0
        for chiave, valore in facts:
            result = await self._db[FACTS].update_one(
                {"scope": scope, "chiave": chiave},
                {
                    "$set": {
                        "valore": valore,
                        "updated_at": datetime.now(UTC),
                        "thread_id": thread_id,
                    },
                    "$setOnInsert": {"created_at": datetime.now(UTC)},
                },
                upsert=True,
            )
            if result.upserted_id is not None or result.modified_count:
                changed += 1
        return changed

    async def facts_of(self, scope: str, limit: int) -> list[dict[str, Any]]:
        """I fatti dello scope, dai piu' recenti.

        Il limite non e' un dettaglio: senza, il contesto di ogni run
        crescerebbe con tutto quello che si e' mai saputo dell'utente.
        """
        cursor = (
            self._db[FACTS]
            .find({"scope": scope}, projection={"chiave": 1, "valore": 1, "_id": 0})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        return [document async for document in cursor]

    async def forget_facts(self, scope: str) -> int:
        result = await self._db[FACTS].delete_many({"scope": scope})
        return int(result.deleted_count)

    async def indexed_upto(self, scope: str, thread_id: str) -> int:
        """Fino a quale posizione i ricordi di questo thread sono gia' indicizzati.

        Segnato sul durevole e non su Redis: l'indice vettoriale e' ricostruibile
        e puo' sparire, il punto a cui si era arrivati no.
        """
        document = await self._db[THREADS].find_one(
            {"scope": scope, "thread_id": thread_id}, projection={"indexed_upto": 1, "_id": 0}
        )
        return int((document or {}).get("indexed_upto") or 0)

    async def set_indexed_upto(self, scope: str, thread_id: str, seq: int) -> None:
        await self._db[THREADS].update_one(
            {"scope": scope, "thread_id": thread_id}, {"$set": {"indexed_upto": seq}}
        )

    async def seqs_of(self, scope: str, thread_id: str) -> list[int]:
        """Le posizioni dei messaggi di un thread, per toglierli dall'indice."""
        return [message.seq for message in await self.history(scope, thread_id)]

    async def threads_of(self, scope: str) -> list[str]:
        """Gli id dei thread di uno scope."""
        return [str(value) for value in await self._db[THREADS].distinct('thread_id', {'scope': scope})]

    async def forget(self, scope: str, thread_id: str) -> int:
        """Cancella una conversazione. Restituisce i bucket rimossi."""
        result = await self._db[TURNS].delete_many({"scope": scope, "thread_id": thread_id})
        await self._db[THREADS].delete_one({"scope": scope, "thread_id": thread_id})
        await self._db[SUMMARIES].delete_many({"scope": scope, "thread_id": thread_id})
        return int(result.deleted_count)

    async def ping(self) -> None:
        """Solleva se il database non risponde."""
        await self._db.command("ping")
