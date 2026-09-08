"""Il knowledge agent esposto via A2A."""
from __future__ import annotations

import logging
import os

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
import httpx
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from agent_framework import Agent
from .executor import KnowledgeExecutor
from .push import NotificheEssenziali
from fastapi import FastAPI

from .agent import build_knowledge_agent, catalogue

logger = logging.getLogger(__name__)

SKILL = AgentSkill(
    id="confronto-linguaggi",
    name="Knowledge base sui linguaggi",
    description="Risponde su tipizzazione, concorrenza, errori ed ecosistema dei linguaggi in catalogo.",
    tags=["linguaggi", "knowledge-base"],
)


def build_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="knowledge",
        description="Sottoagente di knowledge base del laboratorio AG-UI.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(url=base_url, protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[SKILL],
    )


def create_app(agent: Agent | None = None, base_url: str | None = None) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    url = base_url or os.getenv("KNOWLEDGE_BASE_URL", "http://localhost:8200/")
    card = build_agent_card(url)
    executor = KnowledgeExecutor(agent or build_knowledge_agent())
    push_store = InMemoryPushNotificationConfigStore()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
        push_config_store=push_store,
        push_sender=NotificheEssenziali(httpx.AsyncClient(timeout=10.0), push_store),
    )

    app = FastAPI(title="Knowledge agent")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "documenti": sorted(catalogue())}

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
        rest_routes=create_rest_routes(handler),
    )
    logger.info("Knowledge agent pronto su %s, documenti: %s", url, sorted(catalogue()))
    return app
