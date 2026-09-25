"""The subagent events, interleaved with the run's stream."""
from __future__ import annotations

import asyncio

import pytest
from ag_ui.core.events import BaseEvent, EventType, RunFinishedEvent, RunStartedEvent

from demo.server.run_context import LabRunner, subagent_run


class Relay(LabRunner):
    """The real class, with a controlled sequence in place of the framework."""

    def __init__(self, steps) -> None:
        super().__init__(agent=None, state_loader=None)
        self._steps = steps

    async def _framework_events(self, input_data):
        for step in self._steps:
            if callable(step):
                await step()
            else:
                yield step


def run_started() -> BaseEvent:
    return RunStartedEvent(thread_id="t", run_id="r")


def run_finished() -> BaseEvent:
    return RunFinishedEvent(thread_id="t", run_id="r")


async def collect(relay, input_data=None) -> list[BaseEvent]:
    return [event async for event in relay.run(input_data or {})]


@pytest.mark.asyncio
async def test_without_subagents_the_stream_is_untouched():
    events = await collect(Relay([run_started(), run_finished()]))

    assert [e.type for e in events] == [EventType.RUN_STARTED, EventType.RUN_FINISHED]


@pytest.mark.asyncio
async def test_a_subagent_adds_its_start_and_finish():
    async def work():
        async with subagent_run("knowledge", "a question"):
            await asyncio.sleep(0)

    events = await collect(Relay([run_started(), work, run_finished()]))

    assert [e.type for e in events] == [
        EventType.RUN_STARTED,
        EventType.SUBAGENT_STARTED,
        EventType.SUBAGENT_FINISHED,
        EventType.RUN_FINISHED,
    ]


@pytest.mark.asyncio
async def test_the_start_carries_the_name_and_the_pair_shares_the_id():
    async def work():
        async with subagent_run(
            "knowledge", "how does Go do typing?", parent_tool_call_id="c1"
        ):
            await asyncio.sleep(0)

    events = await collect(Relay([work]))

    start, end = events
    assert start.name == "knowledge"
    assert start.description == "how does Go do typing?"
    assert start.parent_tool_call_id == "c1"
    assert start.subagent_run_id == end.subagent_run_id


@pytest.mark.asyncio
async def test_two_subagents_together_produce_two_starts_before_a_finish():
    async def two():
        async def one(delay):
            async with subagent_run("knowledge", f"question {delay}"):
                await asyncio.sleep(delay)

        await asyncio.gather(one(0.05), one(0.02))

    types = [e.type for e in await collect(Relay([two]))]

    assert types[:2] == [EventType.SUBAGENT_STARTED, EventType.SUBAGENT_STARTED]
    assert types[2:] == [EventType.SUBAGENT_FINISHED, EventType.SUBAGENT_FINISHED]


@pytest.mark.asyncio
async def test_a_failing_subagent_emits_an_error_and_lets_it_through():
    async def break_it():
        async with subagent_run("knowledge"):
            raise ConnectionError("subagent down")

    relay = Relay([break_it])

    with pytest.raises(ConnectionError):
        await collect(relay)


@pytest.mark.asyncio
async def test_the_error_event_reaches_the_stream_before_the_failure():
    events: list[BaseEvent] = []

    async def break_it():
        async with subagent_run("knowledge"):
            raise ConnectionError("subagent down")

    relay = Relay([break_it])
    with pytest.raises(ConnectionError):
        async for event in relay.run({}):
            events.append(event)

    assert [e.type for e in events] == [
        EventType.SUBAGENT_STARTED,
        EventType.SUBAGENT_ERROR,
    ]
    assert events[-1].code == "ConnectionError"


@pytest.mark.asyncio
async def test_outside_a_run_the_helper_does_nothing():
    async with subagent_run("knowledge") as run_id:
        assert run_id
