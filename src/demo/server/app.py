"""App FastAPI: espone il master agent via AG-UI su SSE, piu' i log operativi."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_framework import Agent
from agent_framework.ag_ui import (
    AGUIThreadSnapshotStore,
    InMemoryAGUIThreadSnapshotStore,
    add_agent_framework_fastapi_endpoint,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..agents.master import build_master_agent
from ..config import SINGLE_TENANT_SCOPE, get_settings
from ..logging_bridge import LogCollector
from ..memory.remote_store import MemoryServiceSnapshotStore
from .subagent_events import SubagentEventRelay

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

    @app.get("/logs")
    async def logs(cursor: int = 0) -> dict[str, object]:
        """I log applicativi dopo `cursor`.

        Canale separato dallo stream AG-UI: gli eventi CUSTOM del protocollo
        sono riservati al framework e non sono emettibili dal codice applicativo.
        """
        return log_collector.since(cursor)

    runner = SubagentEventRelay(agent=agent or build_master_agent())
    add_agent_framework_fastapi_endpoint(
        app,
        runner,
        "/agui",
        allow_origins=allowed_origins,
        default_state=DEFAULT_STATE,
        snapshot_store=snapshot_store or _default_snapshot_store(),
        snapshot_scope_resolver=_resolve_snapshot_scope,
    )
    return app
