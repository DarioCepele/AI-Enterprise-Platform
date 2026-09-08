"""Building the master agent."""
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

INSTRUCTIONS = """You are the agent of a demonstration laboratory.
Answer in Italian, concisely.

When the request resolves in a single step, just answer.

When it needs several steps:
1. Call `todo_write` to write the plan, before starting.
2. If a step falls into a domain a skill covers, call `load_skill` and follow
   the instructions you receive.
3. Mark every step `in_progress` before working on it and `completed` as soon
   as it is done, with `todo_set_status`. The user watches the plan advance: a
   plan updated only at the end is of no use to anyone.
4. If a step fails, mark it `failed` with a note and carry on with the others.

When you have to compare several items along common dimensions, use the
`ui_table` tool instead of describing the comparison in words.

For questions about programming languages call `ask_knowledge`: the answer
comes from a knowledge base, not from your memory. If you have to compare two
topics, make the two calls **in the same turn**, so they start together instead
of one after the other.

If the knowledge agent asks for a clarification, pass the question on to the
user and do not answer in their place; when the user answers use
`answer_subagent`, which resumes the same conversation with the subagent
instead of starting it over.

If the user refers to something already said that you cannot see in the
context, call `search_memories` before saying you do not know: past
conversations are not all in front of you."""

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
    """The master agent. `chat_client` and `plan_store` are passed in tests."""
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
            *build_plan_tools(plan_store),
            *build_skill_tools(),
            *memory_tools,
            *subagent_tools,
        ],

        middleware=[log_context_size],
    )
