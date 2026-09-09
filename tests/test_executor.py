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

from analysis.executor import ARTIFACT, AnalysisExecutor, assessment


class Update:
    def __init__(self, text: str = "", contents: list | None = None) -> None:
        self.text = text
        self.contents = contents or []


def a_call(call_id: str, tool: str = "measure", arguments: dict | str | None = None):
    return SimpleNamespace(
        type="function_call",
        name=tool,
        call_id=call_id,
        arguments={"values": "[1, 2, 3]"} if arguments is None else arguments,
    )


def a_result(call_id: str, text: str):
    return SimpleNamespace(type="function_result", call_id=call_id, text=text)


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


async def run_executor(agent: FakeAgent, question: str = "how big is this spread?") -> list:
    queue = FakeQueue()
    await AnalysisExecutor(agent).execute(FakeContext(question), queue)
    return queue.events


def states(events: list) -> list[int]:
    return [e.status.state for e in events if isinstance(e, TaskStatusUpdateEvent)]


def artifacts(events: list) -> list:
    return [e.artifact for e in events if isinstance(e, TaskArtifactUpdateEvent)]


def data_of(artifact) -> dict:
    return MessageToDict(next(p.data for p in artifact.parts if p.HasField("data")))


@pytest.mark.asyncio
async def test_the_task_goes_through_its_states():
    events = await run_executor(FakeAgent([Update("The spread is small.")]))

    path = states(events)
    assert path[0] == TaskState.TASK_STATE_SUBMITTED
    assert TaskState.TASK_STATE_WORKING in path
    assert path[-1] == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_the_artifact_carries_the_numbers_the_answer_was_computed_from():
    events = await run_executor(
        FakeAgent(
            [
                Update("", [a_call("c1")]),
                Update("", [a_result("c1", '{"count": 3, "mean": 2.0}')]),
                Update("Three values, mean 2."),
            ]
        )
    )

    produced = artifacts(events)
    assert len(produced) == 1
    assert produced[0].name == ARTIFACT
    data = data_of(produced[0])
    # Prose about arithmetic nobody can check is not an assessment: the call and
    # its result travel with the answer.
    assert data["measurements"][0]["tool"] == "measure"
    assert data["measurements"][0]["result"]["count"] == 3
    assert data["summary"] == "Three values, mean 2."


@pytest.mark.asyncio
async def test_arguments_that_arrive_in_pieces_are_still_understood():
    events = await run_executor(
        FakeAgent(
            [
                Update("", [a_call("c1", arguments="")]),
                Update("", [SimpleNamespace(type="function_call", name="", call_id="c1",
                                            arguments='{"values":')]),
                Update("", [SimpleNamespace(type="function_call", name="", call_id="c1",
                                            arguments=' "[4, 5]"}')]),
                Update("Two values."),
            ]
        )
    )

    data = data_of(artifacts(events)[0])
    assert data["measurements"][0]["arguments"] == {"values": "[4, 5]"}


@pytest.mark.asyncio
async def test_a_tool_that_is_not_a_measurement_is_not_reported_as_one():
    events = await run_executor(
        FakeAgent([Update("", [a_call("c1", tool="something_else")]), Update("Answer.")])
    )

    assert data_of(artifacts(events)[0])["measurements"] == []


@pytest.mark.asyncio
async def test_the_artifact_also_carries_plain_text_for_the_model():
    events = await run_executor(FakeAgent([Update("Answer.")]))

    assert any(part.text == "Answer." for part in artifacts(events)[0].parts)


@pytest.mark.asyncio
async def test_a_question_the_numbers_cannot_settle_asks_back():
    events = await run_executor(
        FakeAgent([Update("[NEEDS-CLARIFICATION] Rispetto a quale periodo?")])
    )

    assert states(events)[-1] == TaskState.TASK_STATE_INPUT_REQUIRED
    assert artifacts(events) == []


@pytest.mark.asyncio
async def test_a_failing_agent_fails_the_task_instead_of_hanging():
    events = await run_executor(FakeAgent([Update("half way")], error=RuntimeError("model down")))

    assert states(events)[-1] == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_an_empty_answer_fails_the_task_instead_of_completing_it():
    events = await run_executor(FakeAgent([]))

    assert states(events)[-1] == TaskState.TASK_STATE_FAILED
    assert artifacts(events) == []


def test_the_artifact_shape_is_documented_by_the_helper():
    parts = assessment("question", "answer", [{"tool": "measure", "arguments": {}, "result": None}])

    data = MessageToDict(next(p.data for p in parts if p.HasField("data")))
    assert data["component"] == "assessment"
    assert data["measurements"][0]["tool"] == "measure"
