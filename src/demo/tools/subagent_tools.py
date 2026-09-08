"""Il tool con cui il master interroga il knowledge agent via A2A."""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import uuid4

from a2a.types import AgentCard
from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

import asyncio

from ..a2a.client import A2AClient, Avanzamento, fetch_agent_card
from ..a2a.push import token_per, url_webhook
from ..config import SINGLE_TENANT_SCOPE, get_settings
from ..server.run_context import subagent_run, thread_of_run

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
        impostazioni = get_settings()
        thread_id = thread_of_run()
        webhook = (
            (
                url_webhook(impostazioni.public_url, SINGLE_TENANT_SCOPE, thread_id),
                token_per(thread_id),
            )
            if impostazioni.public_url and thread_id
            else None
        )
        in_ritardo = False
        pezzi: list[str] = []
        stati: list[str] = []
        artefatti = 0
        schede: list = []
        task_id = ""
        domanda_del_sottoagente = ""

        try:
            remoto = await client()
            async with subagent_run("knowledge", domanda):
                avanzamento: Avanzamento | None = None
                try:
                    async with asyncio.timeout(impostazioni.subagent_wait_seconds):
                        async for avanzamento in remoto.chiedi(domanda, webhook=webhook):
                            task_id = avanzamento.task_id or task_id
                            if not stati or stati[-1] != avanzamento.stato:
                                stati.append(avanzamento.stato)
                            if avanzamento.testo:
                                pezzi.append(avanzamento.testo)
                            if avanzamento.artefatto:
                                artefatti += 1
                                schede.append(avanzamento.artefatto)
                                if avanzamento.artefatto.testo:
                                    pezzi.append(avanzamento.artefatto.testo)
                            if avanzamento.attende_risposta:
                                domanda_del_sottoagente = avanzamento.domanda
                except TimeoutError:
                    in_ritardo = True
        except Exception:
            logger.error("Knowledge agent non raggiungibile per '%s'.", domanda, exc_info=True)
            return Content.from_text(
                "Il knowledge agent non ha risposto: procedi con quello che sai, "
                "dichiarando che questa parte non e' verificata."
            )

        risposta = "".join(pezzi).strip()
        scheda = next(
            (a.dati for a in schede if a.dati and a.dati.get("component") == "scheda"), None
        )
        logger.info(
            "Knowledge agent su '%s': task %s, stati %s, %d artefatti in %.2fs, %d caratteri.",
            domanda,
            task_id[:8] or "?",
            " -> ".join(stati) or "nessuno",
            artefatti,
            time.monotonic() - start,
            len(risposta),
        )


        if in_ritardo:
            logger.info(
                "Il task %s del sottoagente supera l'attesa: si prosegue, l'esito arrivera' via webhook.",
                task_id[:8] or "?",
            )
            parziale = f" Finora ha detto: {risposta}" if risposta else ""
            return Content.from_text(
                "Il knowledge agent sta ancora lavorando e non ho aspettato oltre."
                + parziale
                + " L'esito arrivera' come notifica e sara' disponibile al prossimo turno:"
                " dillo all'utente invece di inventare la risposta."
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
        if scheda is None:
            return Content.from_text(risposta)

        artefatto_id = f"kb_{task_id[:8] or uuid4().hex[:8]}"
        return state_update(
            text=risposta,
            tool_result={
                "component": "scheda",
                "id": artefatto_id,
                "agente": "knowledge",
                "domanda": str(scheda.get("domanda", domanda)),
                "documenti": [str(d) for d in scheda.get("documenti", [])],
                "estratto": str(scheda.get("estratto", risposta)),
            },
            state={
                "artifacts": [
                    {"id": artefatto_id, "component": "scheda", "title": f"knowledge: {domanda[:60]}"}
                ]
            },
        )

    return [interroga_knowledge]
