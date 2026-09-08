"""App FastAPI: espone il master agent via AG-UI su SSE, piu' i log operativi."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from agent_framework import Agent
from agent_framework.ag_ui import (
    AGUIThreadSnapshotStore,
    InMemoryAGUIThreadSnapshotStore,
    add_agent_framework_fastapi_endpoint,
)
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from ..agents.master import build_master_agent
from ..config import SINGLE_TENANT_SCOPE, get_settings
from ..logging_bridge import LogCollector
from ..a2a.client import A2AClient, fetch_agent_card
from ..a2a.push import HEADER, riassunto, terminale, token_valido
from ..memory.remote_store import MemoryServiceSnapshotStore
from .run_context import LabRunner

logger = logging.getLogger(__name__)

DEFAULT_STATE = {"artifacts": [], "plan": {"status": "idle", "steps": []}}

def _resolve_snapshot_scope(request: object) -> str:
    """Lo scope entro cui vivono i thread. Vedi SINGLE_TENANT_SCOPE."""
    return SINGLE_TENANT_SCOPE

def _default_snapshot_store() -> AGUIThreadSnapshotStore:
    """Lo store dei thread: il servizio di memoria se configurato, altrimenti RAM.

    Il ripiego in memoria non e' pigrizia: tiene il laboratorio avviabile con
    il solo master agent, senza dover alzare Mongo e Redis per fare due domande.
    Quale dei due sia attivo si legge nei log all'avvio, perche' la differenza
    -- la conversazione sopravvive al riavvio, oppure no -- e' visibile solo
    quando e' troppo tardi per accorgersene.
    """
    url = get_settings().memory_service_url
    if not url:
        logger.info("Memoria dei thread in RAM: si perde al riavvio del processo.")
        return InMemoryAGUIThreadSnapshotStore()
    logger.info("Memoria dei thread nel servizio di memoria: %s", url)
    return MemoryServiceSnapshotStore(url)

def create_app(
    agent: Agent | None = None,
    collector: LogCollector | None = None,
    snapshot_store: AGUIThreadSnapshotStore | None = None,
) -> FastAPI:
    """Costruisce l'app. `agent`, `collector` e lo store vanno passati nei test."""
    if not logging.getLogger().handlers:
        # Uvicorn configura solo i propri logger: senza questo, `demo.*` finisce
        # nell'handler di ultima istanza, che stampa solo dai WARNING in su e
        # lascia il container muto proprio quando serve leggerlo.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    log_collector = collector if collector is not None else LogCollector()
    log_collector.attach()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:

        try:
            yield
        finally:
            log_collector.detach()

    app = FastAPI(title="Laboratorio AG-UI", lifespan=lifespan)

    allowed_origins = list(get_settings().allowed_origins)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,

        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def esito_del_task(task_id: str) -> str:
        """Va a prendere il risultato: la notifica dice che e' finito, non cosa dice."""
        url = get_settings().knowledge_agent_url
        if not url or not task_id:
            return ""
        try:
            client = A2AClient(await fetch_agent_card(url))
            try:
                return (await client.esito(task_id)).testo
            finally:
                await client.aclose()
        except Exception:
            logger.error("Esito del task %s non recuperabile.", task_id[:8], exc_info=True)
            return ""

    async def annota_in_memoria(
        scope: str, thread_id: str, task_id: str, stato: str, testo: str
    ) -> None:
        """Scrive l'esito nella memoria del thread, cosi' il turno dopo lo vede.

        Senza servizio di memoria resta solo la riga di log: la notifica non si
        perde in silenzio, ma non entra in nessuna conversazione.
        """
        url = get_settings().memory_service_url
        if not url:
            logger.warning("Nessun servizio di memoria: l'esito del task %s resta nei log.", task_id[:8])
            return
        messaggio = (
            f"Il sottoagente ha concluso il task {task_id[:8]} ({stato}).\n{testo}"
            if testo
            else f"Il sottoagente ha chiuso il task {task_id[:8]} con stato {stato}, senza risposta."
        )
        try:
            async with httpx.AsyncClient(base_url=url, timeout=5.0) as http:
                response = await http.post(
                    f"/threads/{thread_id}/messages",
                    json={"role": "assistant", "content": messaggio, "meta": {"task_id": task_id}},
                    headers={"X-Memory-Scope": scope},
                )
                response.raise_for_status()
        except Exception:
            logger.error("Esito del task %s NON annotato in memoria.", task_id[:8], exc_info=True)

    @app.post("/a2a/push/{scope}/{thread_id}")
    async def notifica_sottoagente(
        scope: str,
        thread_id: str,
        request: Request,
        token: str | None = Header(default=None, alias=HEADER),
    ) -> dict[str, str]:
        """Riceve l'esito di un task che il sottoagente ha finito dopo la run.

        La correlazione al thread sta nell'URL, l'autenticita' nel token
        firmato. Senza token valido si rifiuta: un webhook aperto e' un modo
        per far scrivere a chiunque nella memoria di una conversazione.
        """
        notifica = await request.json()
        task_id, stato, testo = riassunto(notifica)
        if not token_valido(thread_id, token):
            logger.warning("Notifica push rifiutata per il task %s: token non valido.", task_id)
            raise HTTPException(status_code=403, detail="token non valido")

        if not terminale(stato):
            # Il sottoagente notifica ogni evento, non solo la fine: scrivere in
            # memoria a ogni avanzamento riempirebbe la conversazione di rumore.
            return {"stato": "avanzamento ignorato"}

        if not testo:
            testo = await esito_del_task(task_id)

        logger.info(
            "Il sottoagente ha concluso il task %s (%s) sul thread %s: %d caratteri.",
            task_id[:8] or "?",
            stato or "stato ignoto",
            thread_id,
            len(testo),
        )
        await annota_in_memoria(scope, thread_id, task_id, stato, testo)
        return {"stato": "ricevuta"}

    @app.get("/logs")
    async def logs(cursor: int = 0) -> dict[str, object]:
        """I log applicativi dopo `cursor`.

        Canale separato dallo stream AG-UI: gli eventi CUSTOM del protocollo
        sono riservati al framework e non sono emettibili dal codice applicativo.
        """
        return log_collector.since(cursor)

    store = snapshot_store or _default_snapshot_store()

    async def stato_del_thread(thread_id: str) -> dict | None:
        snapshot = await store.get(scope=SINGLE_TENANT_SCOPE, thread_id=thread_id)
        stato = getattr(snapshot, "state", None)
        return stato if isinstance(stato, dict) else None

    runner = LabRunner(agent=agent or build_master_agent(), state_loader=stato_del_thread)
    add_agent_framework_fastapi_endpoint(
        app,
        runner,
        "/agui",
        allow_origins=allowed_origins,
        default_state=DEFAULT_STATE,
        snapshot_store=store,
        snapshot_scope_resolver=_resolve_snapshot_scope,
    )
    return app
