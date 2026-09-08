"""Il tool che interroga il knowledge agent via A2A."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentInterface

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


class Update:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeRemote:
    def __init__(self, pezzi: list[str], ritardo: float = 0.0) -> None:
        self.pezzi = pezzi
        self.ritardo = ritardo
        self.domande: list[str] = []

    async def run(self, domanda: str, stream: bool = False):
        self.domande.append(domanda)
        for pezzo in self.pezzi:
            if self.ritardo:
                await asyncio.sleep(self.ritardo)
            yield Update(pezzo)


def tool_with(remote: FakeRemote, streaming: bool = True, loader=None):
    @asynccontextmanager
    async def opener(_card):
        yield remote

    async def load():
        return card(streaming)

    return build_subagent_tools(
        "http://kb:8200/", card_loader=loader or load, remote_opener=opener
    )[0]


@pytest.mark.asyncio
async def test_the_streamed_pieces_become_one_answer():
    remote = FakeRemote(["Le gorou", "tine sono ", "leggere."])
    strumento = tool_with(remote)

    risposta = await strumento.func(domanda="Come funziona la concorrenza in Go?")

    assert risposta.text == "Le goroutine sono leggere."
    assert remote.domande == ["Come funziona la concorrenza in Go?"]


@pytest.mark.asyncio
async def test_the_card_is_fetched_once_and_reused():
    chiamate = []

    async def load():
        chiamate.append(1)
        return card()

    strumento = tool_with(FakeRemote(["ok"]), loader=load)

    await strumento.func(domanda="prima")
    await strumento.func(domanda="seconda")

    assert len(chiamate) == 1


@pytest.mark.asyncio
async def test_a_card_without_streaming_is_reported(caplog):
    strumento = tool_with(FakeRemote(["ok"]), streaming=False)

    with caplog.at_level(logging.WARNING, logger="demo.tools.subagent_tools"):
        await strumento.func(domanda="qualcosa")

    assert "non dichiara streaming" in caplog.text


@pytest.mark.asyncio
async def test_two_questions_in_the_same_turn_run_together():
    remote = FakeRemote(["a", "b", "c"], ritardo=0.15)
    strumento = tool_with(remote)

    start = asyncio.get_running_loop().time()
    await asyncio.gather(
        strumento.func(domanda="tipizzazione"),
        strumento.func(domanda="concorrenza"),
    )
    insieme = asyncio.get_running_loop().time() - start

    start = asyncio.get_running_loop().time()
    await strumento.func(domanda="tipizzazione")
    await strumento.func(domanda="concorrenza")
    in_fila = asyncio.get_running_loop().time() - start

    assert insieme < in_fila * 0.7


@pytest.mark.asyncio
async def test_an_unreachable_subagent_does_not_kill_the_run(caplog):
    @asynccontextmanager
    async def rotto(_card):
        raise ConnectionError("knowledge agent giu'")
        yield

    async def load():
        return card()

    strumento = build_subagent_tools(
        "http://kb:8200/", card_loader=load, remote_opener=rotto
    )[0]

    with caplog.at_level(logging.ERROR, logger="demo.tools.subagent_tools"):
        risposta = await strumento.func(domanda="qualsiasi")

    assert "non ha risposto" in risposta.text
    assert "non e' verificata" in risposta.text
    assert "non raggiungibile" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_answer_is_declared_not_faked():
    strumento = tool_with(FakeRemote([]))

    risposta = await strumento.func(domanda="il nulla")

    assert "non ha prodotto una risposta" in risposta.text
