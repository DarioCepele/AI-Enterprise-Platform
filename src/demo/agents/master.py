"""Costruzione del master agent."""
from __future__ import annotations

from agent_framework import Agent, BaseChatClient
from agent_framework.openai import OpenAIChatCompletionClient

from ..chat_clients.fake import FakeStreamingChatClient
from ..config import get_settings
from ..tools.plan_tools import PlanStore, build_plan_tools
from ..tools.skill_tools import build_skill_tools
from ..tools.ui_tools import get_tools

INSTRUCTIONS = """Sei l'agente di un laboratorio dimostrativo.
Rispondi in italiano, in modo conciso.

Quando la richiesta si risolve in un passo solo, rispondi e basta.

Quando richiede piu' passi:
1. Chiama `todo_write` per scrivere il piano, prima di iniziare.
2. Se un passo ricade in un dominio coperto da una skill, chiama `load_skill`
   e segui le istruzioni che ricevi.
3. Marca ogni passo `in_progress` prima di lavorarci e `completed` appena
   finito, con `todo_set_status`. L'utente vede il piano avanzare: un piano
   aggiornato solo alla fine non serve a nessuno.
4. Se un passo fallisce, marcalo `failed` con una nota e prosegui con gli altri.

Quando devi confrontare piu' elementi lungo dimensioni comuni, usa il tool
`ui_table` invece di descrivere il confronto a parole."""


def _default_chat_client() -> BaseChatClient:
    settings = get_settings()
    if settings.use_fake_client:
        return FakeStreamingChatClient()
    return OpenAIChatCompletionClient(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


def build_master_agent(
    chat_client: BaseChatClient | None = None,
    plan_store: PlanStore | None = None,
) -> Agent:
    """Il master agent. `chat_client` e `plan_store` vanno passati nei test."""
    store = plan_store if plan_store is not None else PlanStore()
    return Agent(
        name="master",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=[*get_tools(), *build_plan_tools(store), *build_skill_tools()],
    )
