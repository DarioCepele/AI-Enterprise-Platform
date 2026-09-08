"""Il sottoagente di knowledge base."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

from agent_framework import Agent, BaseChatClient, Content, FunctionTool, tool
from agent_framework.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

CORPUS = Path(__file__).resolve().parent / "corpus"

INSTRUCTIONS = """Sei un agente di knowledge base interrogato da un altro agente.

Rispondi solo con quello che trovi nei documenti: chiama `leggi_documento` per
ogni argomento che ti serve. Se un argomento non e' nel catalogo, dillo invece
di rispondere a memoria.

Chi ti interroga non e' una persona ma un altro agente, che usera' la tua
risposta dentro un lavoro piu' grande: niente convenevoli, prosa densa e breve.

Se la richiesta e' ambigua al punto che rispondere sarebbe indovinare -- per
esempio non dice di quale linguaggio parli, e il catalogo ne ha piu' d'uno --
rispondi con la sola riga:

[SERVE-CHIARIMENTO] <la domanda che faresti>

Usalo con parsimonia: e' una domanda che risale fino alla persona."""


def catalogue(root: Path = CORPUS) -> dict[str, str]:
    """I documenti disponibili, per nome."""
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.md"))}


def build_knowledge_tools(root: Path = CORPUS) -> list[FunctionTool]:
    documents = catalogue(root)
    listing = ", ".join(documents) or "nessuno"

    @tool
    def leggi_documento(
        nome: Annotated[str, "Il nome del documento, come compare nel catalogo"],
    ) -> Content:
        """Legge un documento della knowledge base.

        Documenti disponibili:
        """
        testo = documents.get(nome)
        if testo is None:
            logger.warning("Documento '%s' non trovato.", nome)
            return Content.from_text(
                f"Il documento '{nome}' non esiste. Disponibili: {listing}."
            )
        logger.info("Documento '%s' letto.", nome)
        return Content.from_text(testo)

    leggi_documento.description = f"{leggi_documento.description}\n{listing}"
    return [leggi_documento]


def _default_chat_client() -> BaseChatClient:
    return OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def build_knowledge_agent(chat_client: BaseChatClient | None = None) -> Agent:
    return Agent(
        name="knowledge",
        description="Risponde su linguaggi di programmazione leggendo una knowledge base locale.",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=build_knowledge_tools(),
    )
