"""More than one subagent, or none: the tools follow the configuration."""
from __future__ import annotations

import logging

import pytest
from a2a.types import AgentCapabilities, AgentCard

from demo.a2a.client import Progress
from demo.config import SubagentConfig
from demo.server.run_context import current_pending
from demo.tools.subagent_tools import build_subagent_tools


def card(name: str, description: str = "") -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        capabilities=AgentCapabilities(streaming=True),
    )


class Remote:
    def __init__(self, answer: str, question: str = "") -> None:
        self.answer = answer
        self.question = question
        self.asked: list[str] = []
        self.resumed: list[str | None] = []
        self.conversations: list[str | None] = []

    async def ask(self, text, task_id=None, context_id=None, webhook=None):
        self.asked.append(text)
        self.resumed.append(task_id)
        self.conversations.append(context_id)
        if self.question and task_id is None:
            yield Progress(
                task_id="t-1",
                context_id="c-1",
                state="input_required",
                raw_state=6,
                question=self.question,
            )
            return
        yield Progress(
            task_id="t-1", context_id="c-1", state="completed", raw_state=3, text=self.answer
        )


def tools_for(configs, remotes, cards=None):
    async def load(config):
        return (cards or {}).get(config.name) or card(config.name)

    return build_subagent_tools(
        configs,
        card_loader=load,
        client_factory=lambda config, _card: remotes[config.name],
    )


KNOWLEDGE = SubagentConfig(name="knowledge", url="http://kb:8200/")
LEGAL = SubagentConfig(name="legal", url="http://legal:8300/")


def test_every_configured_subagent_gets_its_own_tool():
    tools = tools_for(
        [KNOWLEDGE, LEGAL], {"knowledge": Remote("a"), "legal": Remote("b")}
    )

    assert [t.name for t in tools] == ["ask_knowledge", "ask_legal", "answer_subagent"]


def test_without_subagents_there_are_no_tools():
    assert build_subagent_tools([]) == []


def test_the_tool_description_carries_what_the_card_says():
    tools = tools_for(
        [KNOWLEDGE],
        {"knowledge": Remote("a")},
        cards={"knowledge": card("knowledge", "Answers about programming languages.")},
    )

    # The description is loaded with the card, so it costs nothing until the
    # first call; before that the model reads the generic one.
    assert "knowledge" in tools[0].description


@pytest.mark.asyncio
async def test_each_tool_talks_to_its_own_subagent():
    remotes = {"knowledge": Remote("from knowledge"), "legal": Remote("from legal")}
    ask_knowledge, ask_legal, _ = tools_for([KNOWLEDGE, LEGAL], remotes)

    knowledge_answer = await ask_knowledge.func(question="a question")
    legal_answer = await ask_legal.func(question="another question")

    assert knowledge_answer.text == "from knowledge"
    assert legal_answer.text == "from legal"
    assert remotes["legal"].asked == ["another question"]


@pytest.mark.asyncio
async def test_the_answer_goes_back_to_the_subagent_that_asked():
    remotes = {"knowledge": Remote("resumed knowledge"), "legal": Remote("resumed legal")}
    answer_subagent = tools_for([KNOWLEDGE, LEGAL], remotes)[-1]

    token = current_pending.set({"task_id": "t-99", "agent": "legal", "question": "which?"})
    try:
        result = await answer_subagent.func(answer="the answer")
    finally:
        current_pending.reset(token)

    # With two subagents waiting is not enough: the answer has to reach the one
    # that asked, on the task it stopped on.
    assert remotes["legal"].resumed == ["t-99"]
    assert remotes["knowledge"].resumed == []
    assert "resumed legal" in result.text


@pytest.mark.asyncio
async def test_the_answer_goes_back_into_the_conversation_the_task_belongs_to():
    """A task waiting for an answer belongs to a conversation, and so does the answer.

    Without the conversation id the agent opens a new one and refuses the
    message as belonging somewhere else -- which is what it did, in front of a
    user, before this was carried through.
    """
    remote = Remote("resumed", question="which language?")
    ask_knowledge, answer_subagent = tools_for([KNOWLEDGE], {"knowledge": remote})

    stopped = await ask_knowledge.func(question="how does concurrency work?")
    carried = stopped.additional_properties["__ag_ui_tool_result_state__"]
    pending = carried["subagent_pending"]
    assert pending["context_id"] == "c-1"

    token = current_pending.set(pending)
    try:
        await answer_subagent.func(answer="Go")
    finally:
        current_pending.reset(token)

    assert remote.resumed == [None, "t-1"]
    assert remote.conversations == [None, "c-1"]


@pytest.mark.asyncio
async def test_an_answer_for_a_subagent_that_is_gone_says_so(caplog):
    answer_subagent = tools_for([KNOWLEDGE], {"knowledge": Remote("a")})[-1]

    token = current_pending.set({"task_id": "t-99", "agent": "retired", "question": "which?"})
    try:
        with caplog.at_level(logging.WARNING, logger="demo.tools.subagent_tools"):
            result = await answer_subagent.func(answer="the answer")
    finally:
        current_pending.reset(token)

    assert "no longer configured" in result.text
    assert "retired" in caplog.text


@pytest.mark.asyncio
async def test_the_artifact_says_which_subagent_produced_it():
    class WithBriefing(Remote):
        async def ask(self, text, task_id=None, context_id=None, webhook=None):
            from demo.a2a.client import Artifact

            progress = Progress(task_id="t-1", state="working", raw_state=2)
            progress.artifact = Artifact(
                artifact_id="a1",
                name="briefing",
                description="",
                text="the answer",
                data={"component": "briefing", "question": "q", "documents": ["d"], "summary": "s"},
            )
            yield progress

    tools = tools_for([LEGAL], {"legal": WithBriefing("x")})

    result = await tools[0].func(question="a question")

    payload = result.additional_properties["__ag_ui_tool_result_state__"]
    assert payload["artifacts"][0]["title"].startswith("legal:")


def test_a_single_subagent_needs_no_json(monkeypatch):
    monkeypatch.setenv("DEMO_KNOWLEDGE_AGENT_URL", "http://kb:8200/")
    monkeypatch.setenv("DEMO_KNOWLEDGE_SERVICE_TOKEN", "secret")
    monkeypatch.delenv("DEMO_SUBAGENTS", raising=False)

    from demo.config import get_settings

    # The compose file has been passing these two for three stages: a fork with
    # one subagent should not have to learn a JSON list to say so.
    assert get_settings().subagents == (
        SubagentConfig(name="knowledge", url="http://kb:8200/", token="secret"),
    )


def test_an_empty_list_of_subagents_is_not_an_error(monkeypatch):
    monkeypatch.setenv("DEMO_SUBAGENTS", "")
    monkeypatch.delenv("DEMO_KNOWLEDGE_AGENT_URL", raising=False)

    from demo.config import get_settings

    assert get_settings().subagents == ()


def test_the_list_wins_over_the_single_variable(monkeypatch):
    monkeypatch.setenv("DEMO_KNOWLEDGE_AGENT_URL", "http://kb:8200/")
    monkeypatch.setenv("DEMO_SUBAGENTS", '[{"name":"legal","url":"http://legal:8300/"}]')

    from demo.config import get_settings

    assert [s.name for s in get_settings().subagents] == ["legal"]
