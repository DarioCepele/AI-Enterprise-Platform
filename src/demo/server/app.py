"""App FastAPI: espone il master agent via AG-UI su SSE."""
from __future__ import annotations

from agent_framework import Agent
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..agents.master import build_master_agent

# Origini del dev server Next.js.
ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# Stato condiviso iniziale. In tappa 2 `plan` viene popolato dai tool del piano.
DEFAULT_STATE = {"artifacts": []}


def create_app(agent: Agent | None = None) -> FastAPI:
    """Costruisce l'app. `agent` va passato nei test per iniettare il fake client."""
    app = FastAPI(title="Laboratorio AG-UI")
    # In agent-framework-ag-ui 1.2.2 allow_origins non e' ancora implementato.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    add_agent_framework_fastapi_endpoint(
        app,
        agent or build_master_agent(),
        "/agui",
        allow_origins=ALLOWED_ORIGINS,
        default_state=DEFAULT_STATE,
    )
    return app
