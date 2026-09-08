"""Il tool con cui il master interroga il knowledge agent via A2A."""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from a2a.types import AgentCard
from agent_framework import Content, FunctionTool, tool

from ..a2a.client import A2AClient, Avanzamento, fetch_agent_card
from ..server.run_context import subagent_run

logger = logging.getLogger(__name__)


def build_subagent_tools(
    url: str,
    card_loader: Callable[[], Awaitable[AgentCard]] | None = None,
    client_factory: Callable[[AgentCard], Any] | None = None,
) -> list[FunctionTool]:
    load_card = card_loader or (lambda: fetch_agent_card(url))
    make_client = client_factory or (lambda card: A2AClient(card))
    cached: dict[str, Any] = {}

    async def client() -> Any:
        if "client" not in cached:
            card = await load_card()
            if not card.capabilities.streaming:
                logger.warning(
                    "La card di %s non dichiara streaming: le risposte arriveranno intere.", url
                )
            cached["client"] = make_client(card)
        return cached["client"]

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
        stati: list[str] = []
        artefatti = 0
        task_id = ""
        domanda_del_sottoagente = ""

        try:
            remoto = await client()
            async with subagent_run("knowledge", domanda):
                avanzamento: Avanzamento | None = None
                async for avanzamento in remoto.chiedi(domanda):
                    task_id = avanzamento.task_id or task_id
                    if not stati or stati[-1] != avanzamento.stato:
                        stati.append(avanzamento.stato)
                    if avanzamento.testo:
                        pezzi.append(avanzamento.testo)
                    if avanzamento.artefatto:
                        artefatti += 1
                        if avanzamento.artefatto.testo:
                            pezzi.append(avanzamento.artefatto.testo)
                    if avanzamento.attende_risposta:
                        domanda_del_sottoagente = avanzamento.domanda
        except Exception:
            logger.error("Knowledge agent non raggiungibile per '%s'.", domanda, exc_info=True)
            return Content.from_text(
                "Il knowledge agent non ha risposto: procedi con quello che sai, "
                "dichiarando che questa parte non e' verificata."
            )

        risposta = "".join(pezzi).strip()
        logger.info(
            "Knowledge agent su '%s': task %s, stati %s, %d artefatti in %.2fs, %d caratteri.",
            domanda,
            task_id[:8] or "?",
            " -> ".join(stati) or "nessuno",
            artefatti,
            time.monotonic() - start,
            len(risposta),
        )

        if domanda_del_sottoagente:
            return Content.from_text(
                f"Il knowledge agent si e' fermato e chiede: {domanda_del_sottoagente}\n"
                "Riferiscilo all'utente invece di rispondere al posto suo."
            )
        if not risposta:
            return Content.from_text(
                f"Il knowledge agent non ha prodotto una risposta su '{domanda}'."
            )
        return Content.from_text(risposta)

    return [interroga_knowledge]
