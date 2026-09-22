"""The tool the agent uses to search its own memory.

Why a tool and not an automatic injection into the context: pushing the
"probably relevant" memories into every run pays for them always and gets them
right sometimes, and the more there is in the context the less precisely the
model retrieves from it. With a tool, memory is reached **when needed**, and
the one deciding whether it is needed is the model, which has the question in
front of it.
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
    """The `search_memories` tool, bound to a memory service."""
    http = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    @tool
    async def search_memories(
        query: Annotated[
            str,
            "What to look for, phrased as you would say it: the search is by meaning",
        ],
    ) -> Content:
        """Searches past conversations with this user.

        Call it when the user refers to something already said that you cannot
        see in the context -- "as we decided", "that project I told you about"
        -- or when you need a detail you should know and cannot find. The
        search is by meaning, not by exact words.
        """
        try:
            response = await http.post(
                "/search",
                json={"query": query, "limit": 5},
                headers={"X-Memory-Scope": scope},
            )
            response.raise_for_status()
            memories = response.json().get("memories", [])
        except Exception:
            logger.error("Memory search failed for '%s'.", query, exc_info=True)
            return Content.from_text(
                "Memory is unreachable right now: answer with what you know."
            )

        if not memories:
            logger.info("No memory for '%s'.", query)
            return Content.from_text(
                f"No memory found about '{query}'. Do not assume it was ever said."
            )

        logger.info("Memories found for '%s': %d.", query, len(memories))
        lines = "\n".join(
            f"- (similarity {memory['similarity']:.2f}) {memory['text']}"
            for memory in memories
        )
        return Content.from_text(
            f"Memories relevant to '{query}':\n{lines}\n"
            "These are fragments of past conversations, not certainties: verify "
            "them if they matter."
        )

    return [search_memories]
