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


def build_client(uri: str) -> AsyncMongoClient:
    """Il client Mongo del processo. Uno solo, condiviso, mai per richiesta.

    Aprire una connessione costa: TCP, TLS e autenticazione. I valori sotto
    valgono per **questo** profilo -- un container di laboratorio, un'istanza
    sola dell'applicazione, operazioni brevi, concorrenza di poche richieste --
    e vanno rialzati se il servizio viene replicato o messo sotto carico vero.
    """
    return AsyncMongoClient(
        uri,
        # Una manciata di richieste in volo: 20 lascia margine abbondante senza
        # tenere occupata memoria sul server (circa 1 MB per connessione).
        maxPoolSize=20,
        # Due connessioni gia' pronte: evitano l'handshake sulla prima richiesta
        # dopo un periodo di quiete, che nel laboratorio e' la norma.
        minPoolSize=2,
        maxIdleTimeMS=300_000,
        # Fallire in fretta e dirlo: un DB che non risponde deve diventare un
        # errore visibile, non una richiesta appesa.
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

        # Prima strada: c'e' gia' un bucket con posto. Un solo update atomico.
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

        # Seconda strada: serve un bucket nuovo. Se due richieste ci arrivano
        # insieme, l'indice unico ne lascia passare una sola; l'altra rientra
        # dalla prima strada, dove ora il posto c'e'.
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

    async def forget(self, scope: str, thread_id: str) -> int:
        """Cancella una conversazione. Restituisce i bucket rimossi."""
        result = await self._db[TURNS].delete_many({"scope": scope, "thread_id": thread_id})
        await self._db[THREADS].delete_one({"scope": scope, "thread_id": thread_id})
        return int(result.deleted_count)

    async def ping(self) -> None:
        """Solleva se il database non risponde."""
        await self._db.command("ping")
