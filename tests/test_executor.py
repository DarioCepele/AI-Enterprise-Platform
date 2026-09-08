"""The executor: task lifecycle and structured artifact."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from a2a.types import (
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict

from knowledge.executor import ARTIFACT, KnowledgeExecutor, briefing


class Update:
    def __init__(self, text: str = "", contents: list | None = None) -> None:
        self.text = text
        self.contents = contents or []


def a_read(name: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name="read_document", arguments={"name": name})


class FakeAgent:
    def __init__(self, updates: list[Update] | None = None, error: Exception | None = None) -> None:
        self._updates = updates or []
        self._error = error
        self.questions: list[str] = []

    def run(self, question: str, stream: bool = False):
        self.questions.append(question)
        error = self._error
        updates = self._updates

        async def _stream():
            for update in updates:
                yield update
            if error:
                raise error

        return _stream()


class FakeContext:
    task_id = "task-1"
    context_id = "ctx-1"
    current_task = None

    def __init__(self, question: str) -> None:
        self._question = question
        self.message = Message(
            message_id="m-1", role=Role.ROLE_USER, parts=[Part(text=question)]
        )

    def get_user_input(self) -> str:
        return self._question


class FakeQueue:
    """Collects events instead of delivering them: that is what gets asserted."""

    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


async def run_executor(agent: FakeAgent, question: str = "how does Go do typing?") -> list:
    queue = FakeQueue()
    await KnowledgeExecutor(agent).execute(FakeContext(question), queue)
    return queue.events


def states(events: list) -> list[int]:
    return [e.status.state for e in events if isinstance(e, TaskStatusUpdateEvent)]


def artifacts(events: list) -> list:
    return [e.artifact for e in events if isinstance(e, TaskArtifactUpdateEvent)]


@pytest.mark.asyncio
async def test_the_task_goes_through_its_states():
    events = await run_executor(FakeAgent([Update("Go uses error values.")]))

    path = states(events)
    assert path[0] == TaskState.TASK_STATE_SUBMITTED
    assert TaskState.TASK_STATE_WORKING in path
    assert path[-1] == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_the_answer_arrives_as_a_named_artifact_with_data():
    events = await run_executor(
        FakeAgent([Update("", [a_read("go")]), Update("Go uses error values.")])
    )

    produced = artifacts(events)
    assert len(produced) == 1
    artifact = produced[0]
    assert artifact.name == ARTIFACT
    data = MessageToDict(next(p.data for p in artifact.parts if p.HasField("data")))
    assert data["documents"] == ["go"]
    assert data["summary"] == "Go uses error values."


@pytest.mark.asyncio
async def test_the_artifact_also_carries_plain_text_for_the_model():
    events = await run_executor(FakeAgent([Update("Answer.")]))

    artifact = artifacts(events)[0]
    assert any(part.text == "Answer." for part in artifact.parts)


@pytest.mark.asyncio
async def test_the_same_document_read_twice_is_listed_once():
    events = await run_executor(
        FakeAgent([Update("", [a_read("go"), a_read("go")]), Update("Answer.")])
    )

    artifact = artifacts(events)[0]
    data = MessageToDict(next(p.data for p in artifact.parts if p.HasField("data")))
    assert data["documents"] == ["go"]


@pytest.mark.asyncio
async def test_the_text_streams_while_the_task_works():
    events = await run_executor(FakeAgent([Update("first "), Update("second")]))

    spoken = [
        "".join(part.text for part in e.status.message.parts)
        for e in events
        if isinstance(e, TaskStatusUpdateEvent) and e.status.message.parts
    ]
    assert spoken == ["first ", "second"]


@pytest.mark.asyncio
async def test_a_failing_agent_fails_the_task_instead_of_hanging():
    events = await run_executor(FakeAgent([Update("half way")], error=RuntimeError("model down")))

    assert states(events)[-1] == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_an_empty_answer_fails_the_task_instead_of_completing_it():
    events = await run_executor(FakeAgent([]))

    assert states(events)[-1] == TaskState.TASK_STATE_FAILED
    assert artifacts(events) == []


def test_the_card_shape_is_documented_by_the_helper():
    parts = briefing("question", "answer", ["go", "rust"])

    data = MessageToDict(next(p.data for p in parts if p.HasField("data")))
    assert data["component"] == "briefing"
    assert data["documents"] == ["go", "rust"]


def a_read_delta(call_id: str, piece: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", name="read_document", call_id=call_id, arguments=piece
    )


@pytest.mark.asyncio
async def test_arguments_that_arrive_in_pieces_are_still_understood():
    events = await run_executor(
        FakeAgent(
            [
                Update("", [a_read_delta("c1", "")]),
                Update("", [a_read_delta("c1", '{"name"')]),
                Update("", [a_read_delta("c1", ': "rust"}')]),
                Update("Answer."),
            ]
        )
    )

    data = MessageToDict(
        next(p.data for p in artifacts(events)[0].parts if p.HasField("data"))
    )
    assert data["documents"] == ["rust"]


@pytest.mark.asyncio
async def test_two_documents_read_in_one_run_are_both_listed():
    events = await run_executor(
        FakeAgent(
            [
                Update("", [a_read_delta("c1", '{"name": "go"}')]),
                Update("", [a_read_delta("c2", '{"name": "rust"}')]),
                Update("Answer."),
            ]
        )
    )

    data = MessageToDict(
        next(p.data for p in artifacts(events)[0].parts if p.HasField("data"))
    )
    assert data["documents"] == ["go", "rust"]


@pytest.mark.asyncio
async def test_the_name_arrives_only_on_the_first_piece():
    events = await run_executor(
        FakeAgent(
            [
                Update("", [SimpleNamespace(type="function_call", name="read_document", call_id="c1", arguments="")]),
                Update("", [SimpleNamespace(type="function_call", name="", call_id="c1", arguments='{"name": "go"}')]),
                Update("Answer."),
            ]
        )
    )

    data = MessageToDict(next(p.data for p in artifacts(events)[0].parts if p.HasField("data")))
    assert data["documents"] == ["go"]


@pytest.mark.asyncio
async def test_another_tools_arguments_are_not_mistaken_for_ours():
    events = await run_executor(
        FakeAgent(
            [
                Update("", [SimpleNamespace(type="function_call", name="another_tool", call_id="c9", arguments="")]),
                Update("", [SimpleNamespace(type="function_call", name="", call_id="c9", arguments='{"name": "not-ours"}')]),
                Update("Answer."),
            ]
        )
    )

    data = MessageToDict(next(p.data for p in artifacts(events)[0].parts if p.HasField("data")))
    assert data.get("documents", []) == []


@pytest.mark.asyncio
async def test_an_ambiguous_question_leaves_the_task_waiting_for_input():
    events = await run_executor(
        FakeAgent([Update("[NEEDS-CLARIFICATION] Which language are you asking about?")]),
        question="how does concurrency work?",
    )

    # Neither completed nor failed: the task stays open, waiting for someone to
    # answer. That is the hook for human-in-the-loop across agents.
    assert states(events)[-1] == TaskState.TASK_STATE_INPUT_REQUIRED
    assert artifacts(events) == []


@pytest.mark.asyncio
async def test_the_question_travels_with_the_state():
    events = await run_executor(FakeAgent([Update("[NEEDS-CLARIFICATION] Which language are you asking about?")]))

    last = [e for e in events if isinstance(e, TaskStatusUpdateEvent)][-1]
    text = "".join(part.text for part in last.status.message.parts)
    assert text == "Which language are you asking about?"


@pytest.mark.asyncio
async def test_a_marker_without_a_question_still_asks_something():
    events = await run_executor(FakeAgent([Update("[NEEDS-CLARIFICATION]")]))

    last = [e for e in events if isinstance(e, TaskStatusUpdateEvent)][-1]
    assert "precisare" in "".join(part.text for part in last.status.message.parts)
