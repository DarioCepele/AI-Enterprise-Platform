"""Il tool con cui l'agente cerca nella propria memoria.

Perche' un tool e non un'iniezione automatica nel contesto: infilare a ogni run
i ricordi "probabilmente pertinenti" li paga sempre e li azzecca a volte, e piu'
roba c'e' nel contesto meno il modello ne recupera con precisione. Con un tool
la memoria si raggiunge **quando serve**, e chi decide se serve e' il modello,
che la domanda ce l'ha davanti.
"""
from __future__ import annotations

import logging
from typing import Annotated

import httpx
from agent_framework import Content, FunctionTool, tool

logger = logging.getLogger(__name__)


def build_memory_tools(
    base_url: str,
    scope: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> list[FunctionTool]:
    """Il tool `cerca_nei_ricordi`, legato a un servizio di memoria."""
    http = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    @tool
    async def cerca_nei_ricordi(
        domanda: Annotated[
            str, "Cosa cercare, formulato come lo diresti a voce: si cerca per significato"
        ],
    ) -> Content:
        """Cerca nelle conversazioni passate con questo utente.

        Chiamalo quando l'utente si riferisce a qualcosa di gia' detto che non
        vedi nel contesto -- "come avevamo deciso", "quel progetto di cui ti
        parlavo" -- oppure quando ti serve un dettaglio che dovresti sapere e
        non trovi. La ricerca e' per significato, non per parole esatte.
        """
        try:
            response = await http.post(
                "/search",
                json={"query": domanda, "limit": 5},
                headers={"X-Memory-Scope": scope},
            )
            response.raise_for_status()
            ricordi = response.json().get("ricordi", [])
        except Exception:
            logger.error("Ricerca nei ricordi fallita per '%s'.", domanda, exc_info=True)
            return Content.from_text(
                "La memoria non e' raggiungibile in questo momento: rispondi con quello che sai."
            )

        if not ricordi:
            logger.info("Nessun ricordo per '%s'.", domanda)
            return Content.from_text(
                f"Nessun ricordo trovato su '{domanda}'. Non dare per scontato che sia stato detto."
            )

        logger.info("Ricordi trovati per '%s': %d.", domanda, len(ricordi))
        righe = "\n".join(
            f"- (somiglianza {ricordo['somiglianza']:.2f}) {ricordo['testo']}"
            for ricordo in ricordi
        )
        return Content.from_text(
            f"Ricordi pertinenti a '{domanda}':\n{righe}\n"
            "Sono frammenti di conversazioni passate, non certezze: se contano, verificali."
        )

    return [cerca_nei_ricordi]
