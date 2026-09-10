"""Building the master agent."""
from __future__ import annotations

from agent_framework import Agent, BaseChatClient
from agent_framework.openai import OpenAIChatCompletionClient

from ..chat_clients.fake import FakeStreamingChatClient
from ..config import get_settings
from ..telemetry import log_context_size
from ..tools.memory_tools import build_memory_tools
from ..tools.plan_tools import PlanStore, build_plan_tools
from ..tools.process_tools import build_process_tools
from ..tools.subagent_tools import build_subagent_tools
from ..tools.skill_tools import build_skill_tools
from ..tools.ui_tools import get_tools

INSTRUCTIONS = """You are the agent of {product}.
Answer in {language}, concisely.

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

When a question falls in the domain of one of your `ask_*` tools, call that
tool: the answer comes from that agent, not from your memory. Their
descriptions say what each one covers. If you have to compare two topics, make
the two calls **in the same turn**, so they start together instead of one after
the other.

If an agent asks for a clarification, pass the question on to the user and do
not answer in their place; when the user answers use `answer_subagent`, which
resumes the same conversation with that agent instead of starting it over.

If the user refers to something already said that you cannot see in the
context, call `search_memories` before saying you do not know: past
conversations are not all in front of you."""

def instructions_for(product: str, language: str) -> str:
    """The prompt says the product's name and the language it answers in.

    Both are configuration: a template whose agent introduces itself as someone
    else's product, in a language nobody chose, is a template you rewrite before
    using it.
    """
    return INSTRUCTIONS.format(product=product, language=language)

def _default_chat_client() -> BaseChatClient:
    settings = get_settings()
    settings.require_model_access()
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

    subagent_tools = build_subagent_tools(settings.subagents)
    process_tools = build_process_tools(settings.process_service_url, settings.default_scope)
    memory_tools = (
        build_memory_tools(settings.memory_service_url, settings.default_scope)
        if settings.memory_service_url
        else []
    )
    return Agent(
        name="master",
        instructions=instructions_for(settings.product_name, settings.product_language),
        client=chat_client or _default_chat_client(),
        tools=[
            *get_tools(),
            *build_plan_tools(plan_store),
            *build_skill_tools(),
            *memory_tools,
            *subagent_tools,
            *process_tools,
        ],

        middleware=[log_context_size],
    )
