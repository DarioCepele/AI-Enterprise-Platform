"""Costruzione del master agent."""
from __future__ import annotations

from agent_framework import Agent, BaseChatClient
from agent_framework.openai import OpenAIChatCompletionClient

from ..chat_clients.fake import FakeStreamingChatClient
from ..config import get_settings
from ..tools.ui_tools import get_tools

INSTRUCTIONS = """Sei l'agente di un laboratorio dimostrativo.
Rispondi in italiano, in modo conciso.
Quando devi confrontare piu' elementi lungo dimensioni comuni, usa il tool ui_table
invece di descrivere il confronto a parole."""


def _default_chat_client() -> BaseChatClient:
    settings = get_settings()
    if settings.use_fake_client:
        return FakeStreamingChatClient()
    return OpenAIChatCompletionClient(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


def build_master_agent(chat_client: BaseChatClient | None = None) -> Agent:
    """Il master agent. `chat_client` va passato esplicitamente nei test."""
    return Agent(
        name="master",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=get_tools(),
    )
