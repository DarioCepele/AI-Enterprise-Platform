"""The knowledge base subagent."""
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

INSTRUCTIONS = """You are a knowledge base agent queried by another agent.

Answer in Italian, and only with what you find in the documents: call
`read_document` for every topic you need. If a topic is not in the catalogue,
say so instead of answering from memory.

Whoever queries you is not a person but another agent, which will use your
answer inside a larger job: no pleasantries, dense and short prose.

If the request is ambiguous to the point that answering would be guessing --
for instance it does not say which language it is about, and the catalogue
holds more than one -- reply with this single line:

[NEEDS-CLARIFICATION] <the question you would ask, in Italian>

Use it sparingly: it is a question that travels all the way up to a person."""


def catalogue(root: Path = CORPUS) -> dict[str, str]:
    """The available documents, by name."""
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(root.glob("*.md"))
    }


def build_knowledge_tools(root: Path = CORPUS) -> list[FunctionTool]:
    documents = catalogue(root)
    listing = ", ".join(documents) or "none"

    @tool
    def read_document(
        name: Annotated[str, "The document name, as it appears in the catalogue"],
    ) -> Content:
        """Reads a knowledge base document.

        Available documents:
        """
        text = documents.get(name)
        if text is None:
            logger.warning("Document '%s' not found.", name)
            return Content.from_text(
                f"The document '{name}' does not exist. Available: {listing}."
            )
        logger.info("Document '%s' read.", name)
        return Content.from_text(text)

    read_document.description = f"{read_document.description}\n{listing}"
    return [read_document]


def _default_chat_client() -> BaseChatClient:
    return OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def build_knowledge_agent(chat_client: BaseChatClient | None = None) -> Agent:
    return Agent(
        name="knowledge",
        description=(
            "Answers about programming languages by reading a local knowledge base."
        ),
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=build_knowledge_tools(),
    )
