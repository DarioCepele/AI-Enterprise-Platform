"""Chi trasforma i turni vecchi in un riassunto.

La compattazione e' l'unica potatura che **costa un'inferenza**: le altre sono
regole, questa e' una chiamata a un modello. Per questo sta dietro
un'interfaccia sola, viene invocata fuori dal percorso di risposta, e quando
non e' configurata il servizio lo dice invece di fingere.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

# Cosa tenere e cosa buttare. E' la parte piu' delicata del servizio: un
# riassunto che perde una decisione presa fa ripartire l'agente da capo, e chi
# legge non ha modo di accorgersene finche' il danno non e' fatto.
INSTRUCTIONS = """Riassumi la conversazione qui sotto per un agente che deve continuarla.

Conserva:
- le decisioni prese e le conclusioni raggiunte;
- i fatti, i numeri e i nomi che sono stati stabiliti;
- cio' che era in sospeso o non risolto;
- cosa ha chiesto l'utente, con le sue richieste ancora aperte.

Togli:
- le formulazioni esatte e i convenevoli;
- i passaggi intermedi e i risultati dei tool gia' usati;
- tutto cio' che il modello puo' ricavare da solo.

Scrivi in italiano, in prosa asciutta, al massimo dieci righe. Non inventare
nulla che non sia nella conversazione: se un punto non e' chiaro, dillo."""


class Summarizer(Protocol):
    """Da una lista di messaggi a un riassunto in prosa."""

    async def summarize(self, messages: list[dict[str, Any]]) -> str: ...


class NoSummarizer:
    """Nessun modello configurato: nessun riassunto, e si sa perche'."""

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        logger.warning(
            "Riassunto non prodotto: nessun modello configurato (MEMORY_SUMMARY_MODEL). "
            "I turni fuori dalla finestra restano fuori dal contesto."
        )
        return ""


class OpenAICompatibleSummarizer:
    """Riassume con un endpoint Chat Completions.

    Stesso profilo del master agent -- OpenRouter, LM Studio, qualunque cosa
    parli quel protocollo -- ma con la sua chiave e il suo modello: riassumere
    e' un lavoro diverso dal rispondere, e puo' meritare un modello piu' piccolo.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            # Generoso: e' fuori dal percorso di risposta all'utente, e un
            # riassunto tagliato a meta' e' peggio di un riassunto lento.
            timeout=timeout,
        )

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        transcript = "\n".join(_readable(message) for message in messages)
        response = await self._client.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": transcript},
                ],
                # Un riassunto non deve essere creativo.
                "temperature": 0,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return (payload["choices"][0]["message"]["content"] or "").strip()

    async def aclose(self) -> None:
        await self._client.aclose()


def _readable(message: dict[str, Any]) -> str:
    """Un messaggio in una riga leggibile dal modello che riassume.

    Le chiamate ai tool diventano una nota, non JSON: al riassunto interessa
    che un tool sia stato usato e con che esito, non la sua forma sul filo.
    """
    role = message.get("role", "?")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        calls = message.get("toolCalls") or message.get("tool_calls")
        if calls:
            names = ", ".join(
                str((call.get("function") or {}).get("name") or call.get("name") or "?")
                for call in calls
                if isinstance(call, dict)
            )
            return f"[{role}] ha chiamato: {names}"
        content = json.dumps(content, ensure_ascii=False) if content else ""
    return f"[{role}] {content}"
