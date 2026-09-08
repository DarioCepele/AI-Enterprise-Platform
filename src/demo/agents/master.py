"""Costruzione del master agent."""
from __future__ import annotations

from agent_framework import Agent, BaseChatClient
from agent_framework.openai import OpenAIChatCompletionClient

from ..chat_clients.fake import FakeStreamingChatClient
from ..config import SINGLE_TENANT_SCOPE, get_settings
from ..telemetry import log_context_size
from ..tools.memory_tools import build_memory_tools
from ..tools.plan_tools import PlanStore, build_plan_tools
from ..tools.subagent_tools import build_subagent_tools
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
`ui_table` invece di descrivere il confronto a parole.

Per domande su linguaggi di programmazione chiama `interroga_knowledge`: la
risposta viene da una knowledge base, non dalla tua memoria. Se devi confrontare
due argomenti, fai le due chiamate **nello stesso turno**, cosi' partono insieme
invece che una dopo l'altra.

Se l'utente si riferisce a qualcosa di gia' detto che non vedi nel contesto,
chiama `cerca_nei_ricordi` prima di dire che non lo sai: le conversazioni
passate non stanno tutte davanti a te."""

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
    settings = get_settings()

    subagent_tools = (
        build_subagent_tools(settings.knowledge_agent_url)
        if settings.knowledge_agent_url
        else []
    )
    memory_tools = (
        build_memory_tools(settings.memory_service_url, SINGLE_TENANT_SCOPE)
        if settings.memory_service_url
        else []
    )
    return Agent(
        name="master",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=[
            *get_tools(),
            *build_plan_tools(store),
            *build_skill_tools(),
            *memory_tools,
            *subagent_tools,
        ],

        middleware=[log_context_size],
    )
