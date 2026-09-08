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
from .models import NewMessage, Snapshot, StoredMessage, Transcript
from .service import ThreadMemory
from .stores.hot import HotTail
from .stores.mongo import MongoTranscripts, build_client
from .summarizer import OpenAICompatibleSummarizer

logger = logging.getLogger(__name__)


def create_app(memory: ThreadMemory | None = None, settings: Settings | None = None) -> FastAPI:
    """Costruisce l'app. `memory` va passato nei test."""
    config = settings or get_settings()
    # Il servizio iniettato e' pronto subito, senza aspettare il lifespan: i
    # test lo montano su un transport ASGI che il lifespan non lo esegue.
    state: dict[str, object] = {"memory": memory} if memory is not None else {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if memory is not None:
            yield
            return

        # Un client per processo, aperto all'avvio e chiuso allo spegnimento:
        # aprirne uno per richiesta pagherebbe l'handshake ogni volta.
        client: AsyncMongoClient = build_client(config.mongo_uri)
        redis = Redis.from_url(config.redis_uri, decode_responses=True)
        durable = MongoTranscripts(client, config.mongo_database, config.bucket_size)
        await durable.ensure_indexes()
        summarizer = None
        if config.summary_model:
            summarizer = OpenAICompatibleSummarizer(
                config.summary_base_url, config.summary_api_key, config.summary_model
            )
            logger.info("Compattazione attiva con il modello %s.", config.summary_model)
        else:
            # Detto una volta all'avvio invece che a ogni taglio: e' una scelta
            # di configurazione, non un evento.
            logger.warning(
                "Nessun modello per i riassunti (MEMORY_SUMMARY_MODEL): i turni "
                "fuori dalla finestra usciranno dal contesto senza riassunto."
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
        return instance  # type: ignore[return-value]

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
            # 404 e non uno snapshot vuoto: "non so nulla di questo thread" e
            # "questo thread e' vuoto" portano il chiamante a decisioni diverse.
            raise HTTPException(status_code=404, detail="thread sconosciuto")
        return snapshot

    @app.delete("/threads/{thread_id}")
    async def forget_thread(
        thread_id: str,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        return {"buckets_rimossi": await memory_instance.forget(scope, thread_id)}

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
