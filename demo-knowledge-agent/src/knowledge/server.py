"""The knowledge agent exposed over A2A."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)
from agent_framework import Agent
from fastapi import FastAPI, HTTPException
from platform_core.a2a_server import push_components, task_stores
from platform_core.http import BodySizeLimit
from platform_core.observability import configure_logging, configure_telemetry

from .agent import build_knowledge_agent, catalogue
from .config import Settings, get_settings
from .executor import KnowledgeExecutor
from .extended import SCHEME, TOKEN, build_extended_card

logger = logging.getLogger(__name__)

SERVICE_NAME = "knowledge-agent"

# A question between agents is a few kilobytes; nothing legitimate is bigger.
MAX_REQUEST_BYTES = 1_000_000

SKILL = AgentSkill(
    id="language-comparison",
    name="Programming language knowledge base",
    description=(
        "Answers about typing, concurrency, errors and ecosystem of the "
        "languages in the catalogue."
    ),
    tags=["languages", "knowledge-base"],
)


def build_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="knowledge",
        description="Knowledge base subagent of the agent platform.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url=base_url, protocol_binding="JSONRPC", protocol_version="1.0"
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=True, extended_agent_card=True
        ),
        security_schemes={
            SCHEME: SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    description="Service token for the extended card.",
                    scheme="bearer",
                )
            )
        },
        skills=[SKILL],
    )


def create_app(
    agent: Agent | None = None,
    base_url: str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    config = settings or get_settings()
    configure_logging(SERVICE_NAME, as_json=config.json_logs)

    card = build_agent_card(base_url or config.base_url)
    executor = KnowledgeExecutor(agent or build_knowledge_agent(settings=config))
    tasks, webhooks, close_stores = task_stores(
        config.task_store_dsn,
        name="knowledge",
        encryption_key=config.push_encryption_key,
    )
    push_store, push_sender = push_components(webhooks, config.push_policy())
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=tasks,
        agent_card=card,
        push_config_store=push_store,
        push_sender=push_sender,
        extended_agent_card=build_extended_card(card, sorted(catalogue())),
        extended_card_modifier=TOKEN.card_modifier(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await close_stores()

    app = FastAPI(title="Knowledge agent", lifespan=lifespan)
    configure_telemetry(SERVICE_NAME, app, agent_framework=True)
    app.add_middleware(TOKEN.middleware())
    app.add_middleware(BodySizeLimit, default=MAX_REQUEST_BYTES)

    @app.get("/health")
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. It depends on nobody, so it asks nobody."""
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> dict[str, object]:
        """Whether this agent can answer: it needs its corpus, and nothing else."""
        documents = sorted(catalogue())
        if not documents:
            raise HTTPException(status_code=503, detail="the corpus is empty")
        return {"status": "ok", "documents": documents}

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
        rest_routes=create_rest_routes(handler),
    )
    logger.info("Knowledge agent ready, documents: %s", sorted(catalogue()))
    return app
