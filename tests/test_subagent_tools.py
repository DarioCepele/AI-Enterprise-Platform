"""The tool that queries the knowledge agent over A2A."""
from __future__ import annotations

import asyncio
import logging
import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from demo.tools.subagent_tools import build_subagent_tools


def card(streaming: bool = True) -> AgentCard:
    return AgentCard(
        name="knowledge",
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=streaming),
        supported_interfaces=[
            AgentInterface(url="http://kb:8200/", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
    )


from demo.a2a.client import Progress


def a_progress(text: str = "", state: str = "working", raw: int = 2) -> Progress:
    return Progress(task_id="t-1", state=state, raw_state=raw, text=text)


class FakeRemote:
    def __init__(self, pieces: list[str], delay: float = 0.0) -> None:
        self.pieces = pieces
        self.delay = delay
        self.questions: list[str] = []

    async def ask(self, text: str, task_id=None, context_id=None, webhook=None):
        self.questions.append(text)
        yield a_progress(state="accepted", raw=1)
        for piece in self.pieces:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield a_progress(piece)
        yield a_progress(state="completed", raw=3)


def tool_with(remote: FakeRemote, streaming: bool = True, loader=None):
    async def load():
        return card(streaming)

    return build_subagent_tools(
        "http://kb:8200/", card_loader=loader or load, client_factory=lambda _card: remote
    )[0]


@pytest.mark.asyncio
async def test_the_streamed_pieces_become_one_answer():
    remote = FakeRemote(["Gorout", "ines are ", "lightweight."])
    the_tool = tool_with(remote)

    answer = await the_tool.func(question="How does concurrency work in Go?")

    assert answer.text == "Goroutines are lightweight."
    assert remote.questions == ["How does concurrency work in Go?"]


@pytest.mark.asyncio
async def test_the_card_is_fetched_once_and_reused():
    calls = []

    async def load():
        calls.append(1)
        return card()

    the_tool = tool_with(FakeRemote(["ok"]), loader=load)

    await the_tool.func(question="first")
    await the_tool.func(question="second")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_card_without_streaming_is_reported(caplog):
    the_tool = tool_with(FakeRemote(["ok"]), streaming=False)

    with caplog.at_level(logging.WARNING, logger="demo.tools.subagent_tools"):
        await the_tool.func(question="something")

    assert "does not declare streaming" in caplog.text


@pytest.mark.asyncio
async def test_two_questions_in_the_same_turn_run_together():
    remote = FakeRemote(["a", "b", "c"], delay=0.15)
    the_tool = tool_with(remote)

    start = asyncio.get_running_loop().time()
    await asyncio.gather(
        the_tool.func(question="typing"),
        the_tool.func(question="concurrency"),
    )
    together = asyncio.get_running_loop().time() - start

    start = asyncio.get_running_loop().time()
    await the_tool.func(question="typing")
    await the_tool.func(question="concurrency")
    queued = asyncio.get_running_loop().time() - start

    assert together < queued * 0.7


@pytest.mark.asyncio
async def test_an_unreachable_subagent_does_not_kill_the_run(caplog):
    class Broken:
        async def ask(self, text, task_id=None, context_id=None, webhook=None):
            raise ConnectionError("knowledge agent down")
            yield

    async def load():
        return card()

    the_tool = build_subagent_tools(
        "http://kb:8200/", card_loader=load, client_factory=lambda _card: Broken()
    )[0]

    with caplog.at_level(logging.ERROR, logger="demo.tools.subagent_tools"):
        answer = await the_tool.func(question="anything")

    assert "did not answer" in answer.text
    assert "not verified" in answer.text
    assert "unreachable" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_answer_is_declared_not_faked():
    the_tool = tool_with(FakeRemote([]))

    answer = await the_tool.func(question="nothing at all")

    assert "produced no answer" in answer.text


class RemoteWithArtifacts(FakeRemote):
    async def ask(self, text, task_id=None, context_id=None, webhook=None):
        from demo.a2a.client import Artifact

        self.questions.append(text)
        yield a_progress(state="accepted", raw=1)
        a = a_progress()
        a.artifact = Artifact(
            artifact_id="a1", name="briefing", description="", text="from the document"
        )
        yield a
        yield a_progress(state="completed", raw=3)


class RemoteThatAsks(FakeRemote):
    async def ask(self, text, task_id=None, context_id=None, webhook=None):
        self.questions.append(text)
        a = a_progress(state="waiting for an answer", raw=6)
        a.question = "Which version of Go?"
        yield a


@pytest.mark.asyncio
async def test_the_text_that_arrives_as_an_artifact_is_not_lost():
    the_tool = tool_with(RemoteWithArtifacts([]))

    answer = await the_tool.func(question="something")

    assert "from the document" in answer.text


@pytest.mark.asyncio
async def test_the_task_lifecycle_ends_up_in_the_logs(caplog):
    the_tool = tool_with(FakeRemote(["ok"]))

    with caplog.at_level(logging.INFO, logger="demo.tools.subagent_tools"):
        await the_tool.func(question="something")

    assert "accepted -> working -> completed" in caplog.text
    assert "task t-1" in caplog.text


@pytest.mark.asyncio
async def test_a_subagent_that_asks_is_reported_not_answered_for():
    the_tool = tool_with(RemoteThatAsks([]))

    answer = await the_tool.func(question="something")

    assert "stopped and asks" in answer.text
    assert "Which version of Go?" in answer.text


class RemoteThatResumes(FakeRemote):
    def __init__(self) -> None:
        super().__init__([])
        self.resumed: list[str | None] = []

    async def ask(self, text, task_id=None, context_id=None, webhook=None):
        self.resumed.append(task_id)
        self.questions.append(text)
        yield a_progress("With Go: goroutines and channels.", state="completed", raw=3)


def tools_with(remote):
    async def load():
        return card()

    return build_subagent_tools(
        "http://kb:8200/", card_loader=load, client_factory=lambda _card: remote
    )


@pytest.mark.asyncio
async def test_an_asking_subagent_is_remembered_in_the_thread_state():
    the_tool = tool_with(RemoteThatAsks([]))

    result = await the_tool.func(question="how does concurrency work?")

    state = result.additional_properties["__ag_ui_tool_result_state__"]
    assert state["subagent_pending"]["task_id"] == "t-1"
    assert "Which version of Go?" in state["subagent_pending"]["question"]


@pytest.mark.asyncio
async def test_the_answer_resumes_the_same_task():
    from demo.server.run_context import current_pending

    remote = RemoteThatResumes()
    answer_tool = tools_with(remote)[1]
    token = current_pending.set({"task_id": "t-99", "agent": "knowledge", "question": "which?"})
    try:
        result = await answer_tool.func(answer="Go")
    finally:
        current_pending.reset(token)

    # The same task, not a new one: that is what makes the user's answer a
    # continuation and not another conversation.
    assert remote.resumed == ["t-99"]
    assert "goroutines" in result.text


@pytest.mark.asyncio
async def test_resuming_clears_the_pending_state():
    from demo.server.run_context import current_pending

    answer_tool = tools_with(RemoteThatResumes())[1]
    token = current_pending.set({"task_id": "t-99"})
    try:
        result = await answer_tool.func(answer="Go")
    finally:
        current_pending.reset(token)

    assert result.additional_properties["__ag_ui_tool_result_state__"]["subagent_pending"] == {}


@pytest.mark.asyncio
async def test_answering_with_nobody_waiting_says_so():
    answer_tool = tools_with(RemoteThatResumes())[1]

    result = await answer_tool.func(answer="Go")

    assert "No subagent is waiting" in result.text


class RemoteWithExtendedCard(FakeRemote):
    def __init__(self, skills: list[AgentSkill] | None = None) -> None:
        super().__init__(["ok"])
        self.received_token = ""
        self._skills = skills

    async def extended_card(self, token: str) -> AgentCard | None:
        self.received_token = token
        if self._skills is None:
            return None
        return AgentCard(name="knowledge", skills=self._skills)


CATALOGUE = AgentSkill(
    id="catalogue",
    name="Document catalogue",
    description="Lists the indexed documents: go, python, rust",
)


def tool_with_card(remote, token: str, extended: bool = True):
    async def load():
        return AgentCard(
            name="knowledge",
            capabilities=AgentCapabilities(streaming=True, extended_agent_card=extended),
        )

    return build_subagent_tools(
        "http://kb:8200/", card_loader=load, client_factory=lambda _card: remote
    )[0]


@pytest.mark.asyncio
async def test_the_catalogue_reaches_the_tool_description(monkeypatch):
    monkeypatch.setenv("DEMO_KNOWLEDGE_SERVICE_TOKEN", "secret")
    remote = RemoteWithExtendedCard([CATALOGUE])
    the_tool = tool_with_card(remote, "secret")

    await the_tool.func(question="anything")

    # The model knows what there is to ask for only because the master authenticated.
    assert remote.received_token == "secret"
    assert "go, python, rust" in the_tool.description


@pytest.mark.asyncio
async def test_without_a_token_the_description_stays_generic(monkeypatch):
    monkeypatch.setenv("DEMO_KNOWLEDGE_SERVICE_TOKEN", "")
    remote = RemoteWithExtendedCard([CATALOGUE])
    the_tool = tool_with_card(remote, "")

    await the_tool.func(question="anything")

    assert remote.received_token == ""
    assert "catalogue" not in the_tool.description.lower()


@pytest.mark.asyncio
async def test_a_refused_extended_card_does_not_stop_the_tool(monkeypatch):
    monkeypatch.setenv("DEMO_KNOWLEDGE_SERVICE_TOKEN", "wrong")
    remote = RemoteWithExtendedCard(None)
    the_tool = tool_with_card(remote, "wrong")

    answer = await the_tool.func(question="anything")

    # Not being able to see the extended view is no reason not to query the agent.
    assert "ok" in answer.text
    assert the_tool.description.startswith("Queries")
