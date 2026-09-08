"""Gli eventi dei sottoagenti, intrecciati allo stream della run."""
from __future__ import annotations

import asyncio

import pytest
from ag_ui.core.events import BaseEvent, EventType, RunFinishedEvent, RunStartedEvent

from demo.server.run_context import LabRunner, subagent_run


class Relay(LabRunner):
    """La classe vera, con al posto del framework una sequenza controllata."""

    def __init__(self, passi) -> None:
        super().__init__(agent=None, plan_loader=None)
        self._passi = passi

    async def _framework_events(self, input_data):
        for passo in self._passi:
            if callable(passo):
                await passo()
            else:
                yield passo


def run_started() -> BaseEvent:
    return RunStartedEvent(thread_id="t", run_id="r")


def run_finished() -> BaseEvent:
    return RunFinishedEvent(thread_id="t", run_id="r")


async def raccogli(relay, input_data=None) -> list[BaseEvent]:
    return [event async for event in relay.run(input_data or {})]


@pytest.mark.asyncio
async def test_without_subagents_the_stream_is_untouched():
    eventi = await raccogli(Relay([run_started(), run_finished()]))

    assert [e.type for e in eventi] == [EventType.RUN_STARTED, EventType.RUN_FINISHED]


@pytest.mark.asyncio
async def test_a_subagent_adds_its_start_and_finish():
    async def lavora():
        async with subagent_run("knowledge", "domanda"):
            await asyncio.sleep(0)

    eventi = await raccogli(Relay([run_started(), lavora, run_finished()]))

    assert [e.type for e in eventi] == [
        EventType.RUN_STARTED,
        EventType.SUBAGENT_STARTED,
        EventType.SUBAGENT_FINISHED,
        EventType.RUN_FINISHED,
    ]


@pytest.mark.asyncio
async def test_the_start_carries_the_name_and_the_pair_shares_the_id():
    async def lavora():
        async with subagent_run("knowledge", "come tipizza Go?", parent_tool_call_id="c1"):
            await asyncio.sleep(0)

    eventi = await raccogli(Relay([lavora]))

    inizio, fine = eventi
    assert inizio.name == "knowledge"
    assert inizio.description == "come tipizza Go?"
    assert inizio.parent_tool_call_id == "c1"
    assert inizio.subagent_run_id == fine.subagent_run_id


@pytest.mark.asyncio
async def test_two_subagents_together_produce_two_starts_before_a_finish():
    async def due():
        async def uno(ritardo):
            async with subagent_run("knowledge", f"domanda {ritardo}"):
                await asyncio.sleep(ritardo)

        await asyncio.gather(uno(0.05), uno(0.02))

    tipi = [e.type for e in await raccogli(Relay([due]))]

    assert tipi[:2] == [EventType.SUBAGENT_STARTED, EventType.SUBAGENT_STARTED]
    assert tipi[2:] == [EventType.SUBAGENT_FINISHED, EventType.SUBAGENT_FINISHED]


@pytest.mark.asyncio
async def test_a_failing_subagent_emits_an_error_and_lets_it_through():
    async def rompi():
        async with subagent_run("knowledge"):
            raise ConnectionError("sottoagente giu'")

    relay = Relay([rompi])

    with pytest.raises(ConnectionError):
        await raccogli(relay)


@pytest.mark.asyncio
async def test_the_error_event_reaches_the_stream_before_the_failure():
    eventi: list[BaseEvent] = []

    async def rompi():
        async with subagent_run("knowledge"):
            raise ConnectionError("sottoagente giu'")

    relay = Relay([rompi])
    with pytest.raises(ConnectionError):
        async for event in relay.run({}):
            eventi.append(event)

    assert [e.type for e in eventi] == [EventType.SUBAGENT_STARTED, EventType.SUBAGENT_ERROR]
    assert eventi[-1].code == "ConnectionError"


@pytest.mark.asyncio
async def test_outside_a_run_the_helper_does_nothing():
    async with subagent_run("knowledge") as run_id:
        assert run_id
