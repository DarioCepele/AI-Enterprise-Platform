"""App FastAPI: espone il master agent via AG-UI su SSE, piu' i log operativi."""
from __future__ import annotations

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
from ..config import get_settings
from ..logging_bridge import LogCollector

# La forma iniziale dello stato condiviso. `plan` c'e' gia' vuoto perche' il
# pannello del frontend possa renderlo prima del primo todo_write, invece di
# doverne indovinare la forma.
DEFAULT_STATE = {"artifacts": [], "plan": {"status": "idle", "steps": []}}

# Confine di autorizzazione degli snapshot. Il framework rifiuta uno store senza
# un resolver proprio perche' il thread id NON e' un'autorizzazione: chi conosce
# l'id di un thread altrui non deve poterne leggere la storia.
#
# Il laboratorio gira senza autenticazione, quindi lo scope e' dichiarato uno
# solo per tutto il processo. In produzione questa funzione restituisce
# l'identita' verificata della richiesta -- il claim `sub` del token, l'id del
# tenant -- prendendola da una dependency di autenticazione sull'endpoint
# (`dependencies=[Depends(...)]`), mai da un header scelto dal client.
SINGLE_TENANT_SCOPE = "laboratorio-locale"


def _resolve_snapshot_scope(request: object) -> str:
    """Lo scope entro cui vivono i thread. Vedi SINGLE_TENANT_SCOPE."""
    return SINGLE_TENANT_SCOPE


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
        # attach() e' gia' avvenuto sopra, a costruzione dell'app: qui serve
        # solo il detach allo shutdown, altrimenti l'handler resta agganciato
        # a logging.getLogger("demo") -- un singleton di processo -- per
        # sempre, uno in piu' ad ogni create_app().
        try:
            yield
        finally:
            log_collector.detach()

    app = FastAPI(title="Laboratorio AG-UI", lifespan=lifespan)

    # Le origini non sono hardcoded: il dev server di Next slitta di porta se la
    # 3000 e' occupata, e un'origine sbagliata fallisce solo nel browser.
    allowed_origins = list(get_settings().allowed_origins)
    # In agent-framework-ag-ui 1.2.2 allow_origins non e' ancora implementato.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        # GET serve a /logs: senza, il preflight fallisce solo nel browser.
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

    # La conversazione la possiede il server. Il client manda solo il turno
    # nuovo: l'adattatore ricompone la storia dallo snapshot del thread.
    # Store in memoria: un solo snapshot per (scope, thread), niente durata oltre
    # il processo. In produzione si sostituisce con uno store durevole senza
    # toccare l'agente -- la firma e' il protocollo AGUIThreadSnapshotStore.
    add_agent_framework_fastapi_endpoint(
        app,
        agent or build_master_agent(),
        "/agui",
        allow_origins=allowed_origins,
        default_state=DEFAULT_STATE,
        snapshot_store=snapshot_store or InMemoryAGUIThreadSnapshotStore(),
        snapshot_scope_resolver=_resolve_snapshot_scope,
    )
    return app
