"""The analysis agent exposed over A2A."""
from __future__ import annotations

import logging
import os

import httpx
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
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
from fastapi import FastAPI

from .agent import build_analysis_agent
from .executor import AnalysisExecutor
from .extended import SCHEME, ServiceTokenOnly, build_extended_card, card_for_the_caller
from .observability import configure_logging, configure_tracing
from .push import EssentialNotifications

logger = logging.getLogger(__name__)

SERVICE_NAME = "analysis-agent"

# What this agent computes. It is the answer to "why two subagents": one reads
# what somebody wrote down, this one works on what arrives in the question.
MEASURES = (
    "count",
    "min",
    "max",
    "mean",
    "median",
    "spread",
    "outliers",
    "weighted ranking",
)

SKILL = AgentSkill(
    id="numbers",
    name="Measurement and comparison",
    description=(
        "Measures a series of numbers and weighs options against each other on given "
        "criteria, saying what the numbers do not say."
    ),
    tags=["numbers", "comparison"],
)


def build_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="analysis",
        description="Analysis subagent of the AG-UI laboratory.",
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


def create_app(agent: Agent | None = None, base_url: str | None = None) -> FastAPI:
    as_json = os.getenv("ANALYSIS_JSON_LOGS", "").lower() == "true"
    configure_logging(SERVICE_NAME, as_json=as_json)

    configured_url = os.getenv("ANALYSIS_BASE_URL", "http://localhost:8400/")
    url = base_url or configured_url
    card = build_agent_card(url)
    executor = AnalysisExecutor(agent or build_analysis_agent())
    push_store = InMemoryPushNotificationConfigStore()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
        push_config_store=push_store,
        push_sender=EssentialNotifications(httpx.AsyncClient(timeout=10.0), push_store),
        extended_agent_card=build_extended_card(card, MEASURES),
        extended_card_modifier=card_for_the_caller,
    )

    app = FastAPI(title="Analysis agent")
    configure_tracing(SERVICE_NAME, app)
    app.add_middleware(ServiceTokenOnly)

    @app.get("/health")
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. It depends on nobody, so it asks nobody."""
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> dict[str, object]:
        """Whether this agent can answer.

        It reads no corpus and calls no database: what it needs arrives in the
        request, so being alive and being ready are the same thing here. Saying
        so plainly is better than inventing a check that always passes.
        """
        return {"status": "ok", "measures": list(MEASURES)}

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
        rest_routes=create_rest_routes(handler),
    )
    logger.info("Analysis agent ready on %s, measures: %s", url, ", ".join(MEASURES))
    return app
