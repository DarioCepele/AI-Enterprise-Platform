"""Il tool con cui il master interroga il knowledge agent via A2A."""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from a2a.types import AgentCard
from agent_framework import Content, FunctionTool, tool
from agent_framework.a2a import A2AAgent
from google.protobuf.json_format import ParseDict

logger = logging.getLogger(__name__)

CARD_PATH = ".well-known/agent-card.json"


async def fetch_agent_card(url: str, timeout: float = 10.0) -> AgentCard:
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(f"{url.rstrip('/')}/{CARD_PATH}")
        response.raise_for_status()
        return ParseDict(response.json(), AgentCard(), ignore_unknown_fields=True)


@asynccontextmanager
async def open_remote(card: AgentCard, name: str = "knowledge"):
    async with A2AAgent(name=name, agent_card=card) as remote:
        yield remote


def build_subagent_tools(
    url: str,
    card_loader: Callable[[], Awaitable[AgentCard]] | None = None,
    remote_opener: Any = None,
) -> list[FunctionTool]:
    load_card = card_loader or (lambda: fetch_agent_card(url))
    open_it = remote_opener or open_remote
    cached: dict[str, AgentCard] = {}

    async def card() -> AgentCard:
        if "card" not in cached:
            cached["card"] = await load_card()
            capabilities = cached["card"].capabilities
            if not capabilities.streaming:
                logger.warning(
                    "La card di %s non dichiara streaming: le risposte arriveranno intere.", url
                )
        return cached["card"]

    @tool
    async def interroga_knowledge(
        domanda: Annotated[str, "La domanda da girare al knowledge agent, autosufficiente"],
    ) -> Content:
        """Interroga l'agente di knowledge base su un argomento.

        Ogni chiamata e' indipendente: il sottoagente non vede la conversazione,
        quindi la domanda deve bastare a se stessa. Per confrontare due
        argomenti chiamalo due volte nello stesso turno, cosi' le due
        interrogazioni partono insieme invece che una dopo l'altra.
        """
        start = time.monotonic()
        pezzi: list[str] = []
        aggiornamenti = 0
        try:
            remote_card = await card()
            async with open_it(remote_card) as remote:
                async for update in remote.run(domanda, stream=True):
                    aggiornamenti += 1
                    testo = getattr(update, "text", None)
                    if testo:
                        pezzi.append(testo)
        except Exception:
            logger.error("Knowledge agent non raggiungibile per '%s'.", domanda, exc_info=True)
            return Content.from_text(
                "Il knowledge agent non ha risposto: procedi con quello che sai, "
                "dichiarando che questa parte non e' verificata."
            )

        risposta = "".join(pezzi).strip()
        logger.info(
            "Knowledge agent su '%s': %d aggiornamenti in %.2fs, %d caratteri.",
            domanda,
            aggiornamenti,
            time.monotonic() - start,
            len(risposta),
        )
        if not risposta:
            return Content.from_text(f"Il knowledge agent non ha prodotto una risposta su '{domanda}'.")
        return Content.from_text(risposta)

    return [interroga_knowledge]
