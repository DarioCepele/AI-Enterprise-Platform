"""API HTTP del servizio di memoria.

**Servizio interno.** Non va esposto al browser ne' a internet: chi lo chiama
dichiara lo scope, e il servizio si fida. E' lo stesso modello di un database --
la fiducia sta nella rete e nell'autenticazione fra servizi, non nel client.
Chi lo mette in produzione lo tiene su rete privata e davanti gli mette mTLS o
un token di servizio; lo scope resta l'identita' verificata dal chiamante, mai
un valore scelto dall'utente finale.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from pymongo import AsyncMongoClient
from redis.asyncio import Redis

from .config import Settings, get_settings
from .curation import ContextPolicy
from .models import NewMessage, SearchQuery, Snapshot, StoredMessage, Transcript
from .service import ThreadMemory
from .stores.hot import HotTail
from .embedder import OpenAICompatibleEmbedder
from .stores.mongo import MongoTranscripts, build_client
from .stores.vectors import RedisMemories
from .summarizer import OpenAICompatibleSummarizer

logger = logging.getLogger(__name__)


def create_app(memory: ThreadMemory | None = None, settings: Settings | None = None) -> FastAPI:
    """Costruisce l'app. `memory` va passato nei test."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = settings or get_settings()
    state: dict[str, object] = {"memory": memory} if memory is not None else {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if memory is not None:
            yield
            return

        client: AsyncMongoClient = build_client(config.mongo_uri)
        redis = Redis.from_url(config.redis_uri, decode_responses=True)
        memories = RedisMemories(redis) if config.embedding_model else None
        embedder = (
            OpenAICompatibleEmbedder(
                config.summary_base_url, config.summary_api_key, config.embedding_model
            )
            if config.embedding_model
            else None
        )
        if memories is None:
            logger.warning(
                "Nessun modello di embedding (MEMORY_EMBEDDING_MODEL): la ricerca "
                "semantica nei ricordi non sara' disponibile."
            )
        durable = MongoTranscripts(client, config.mongo_database, config.bucket_size)
        await durable.ensure_indexes()
        summarizer = None
        if config.summary_model:
            summarizer = OpenAICompatibleSummarizer(
                config.summary_base_url, config.summary_api_key, config.summary_model
            )
            logger.info(
                "Compattazione e fatti duraturi attivi con il modello %s.", config.summary_model
            )
        else:
            logger.warning(
                "Nessun modello per i riassunti (MEMORY_SUMMARY_MODEL): i turni "
                "fuori dalla finestra usciranno dal contesto senza riassunto, e "
                "non si imparera' nessun fatto duraturo."
            )
        state["memory"] = ThreadMemory(
            durable,
            HotTail(redis, config.hot_tail_seconds, config.hot_tail_messages),
            ContextPolicy(
                drop_reasoning=config.drop_reasoning,
                keep_tool_results=config.keep_tool_results,
                max_messages=config.max_context_messages,
            ),
            summarizer,
            summarizer,
            config.max_facts,
            embedder,
            memories,
        )
        try:
            yield
        finally:
            await client.close()
            await redis.aclose()

    app = FastAPI(title="Memoria conversazionale", lifespan=lifespan)

    def current_memory() -> ThreadMemory:
        instance = state.get("memory")
        if instance is None:
            raise HTTPException(status_code=503, detail="servizio non inizializzato")
        return instance

    def current_scope(
        scope: str = Header(
            ...,
            alias="X-Memory-Scope",
            description="Confine di autorizzazione: l'identita' verificata dal chiamante.",
        ),
    ) -> str:
        if not scope.strip():
            raise HTTPException(status_code=400, detail="scope vuoto")
        return scope

    @app.get("/health")
    async def health(
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, str]:
        """Stato reale delle dipendenze, non un 200 di cortesia."""
        try:
            return await memory_instance.check()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"memoria durevole non raggiungibile: {error}"
            ) from error

    @app.post("/threads/{thread_id}/messages", status_code=201)
    async def append_message(
        thread_id: str,
        message: NewMessage,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> StoredMessage:
        return await memory_instance.append(scope, thread_id, message)

    @app.get("/threads/{thread_id}/messages")
    async def read_tail(
        thread_id: str,
        limit: int = Query(default=50, ge=1, le=500),
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> Transcript:
        return await memory_instance.tail(scope, thread_id, limit)

    @app.put("/threads/{thread_id}/snapshot")
    async def save_snapshot(
        thread_id: str,
        snapshot: Snapshot,
        background: BackgroundTasks,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        """Assorbe lo stato completo del thread. I turni gia' visti non tornano.

        La compattazione parte **dopo** la risposta: e' una chiamata a un
        modello, e tenerla qui dentro manda in timeout il client -- misurato
        sul campo, non temuto. Il riassunto serve al turno successivo.
        """
        written = await memory_instance.save_snapshot(scope, thread_id, snapshot)
        background.add_task(memory_instance.compact_if_needed, scope, thread_id)
        return {"turni_nuovi": written}

    @app.get("/threads/{thread_id}/snapshot")
    async def read_snapshot(
        thread_id: str,
        raw: bool = Query(
            default=False,
            description="Transcript integrale invece del contesto potato.",
        ),
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> Snapshot:
        snapshot = await memory_instance.read_snapshot(scope, thread_id, raw=raw)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="thread sconosciuto")
        return snapshot

    @app.delete("/threads/{thread_id}")
    async def forget_thread(
        thread_id: str,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        return {"buckets_rimossi": await memory_instance.forget(scope, thread_id)}

    @app.post("/search")
    async def search(
        query: SearchQuery,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, list[dict[str, object]]]:
        """Cerca nei ricordi dello scope per significato.

        POST e non GET con la domanda nell'URL: le domande finiscono nei log di
        accesso dei proxy, e qui la domanda e' contenuto di una conversazione.
        """
        trovati = await memory_instance.search_memories(scope, query.query, query.limit)
        return {
            "ricordi": [
                {
                    "thread_id": ricordo.thread_id,
                    "seq": ricordo.seq,
                    "testo": ricordo.testo,
                    "somiglianza": round(ricordo.somiglianza, 4),
                }
                for ricordo in trovati
            ]
        }

    @app.delete("/scope")
    async def forget_scope(
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        """Dimentica tutti i thread di uno scope.

        Non esiste un modo di cancellare piu' scope in una chiamata sola, ed e'
        voluto: cancellare attraverso un confine di autorizzazione e' proprio
        l'operazione che quel confine esiste per impedire.
        """
        return {"thread_rimossi": await memory_instance.forget_scope(scope)}

    return app
