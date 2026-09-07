"""App FastAPI: espone il master agent via AG-UI su SSE, piu' i log operativi."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_framework import Agent
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..agents.master import build_master_agent
from ..config import get_settings
from ..logging_bridge import LogCollector

# La forma iniziale dello stato condiviso. `plan` c'e' gia' vuoto perche' il
# pannello del frontend possa renderlo prima del primo todo_write, invece di
# doverne indovinare la forma.
DEFAULT_STATE = {"artifacts": [], "plan": {"status": "idle", "steps": []}}


def create_app(
    agent: Agent | None = None,
    collector: LogCollector | None = None,
) -> FastAPI:
    """Costruisce l'app. `agent` e `collector` vanno passati nei test."""
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

    add_agent_framework_fastapi_endpoint(
        app,
        agent or build_master_agent(),
        "/agui",
        allow_origins=allowed_origins,
        default_state=DEFAULT_STATE,
    )
    return app
