"""Il tool che interroga il knowledge agent via A2A."""
from __future__ import annotations

import asyncio
import logging
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


from demo.a2a.client import Avanzamento


def avanzamento(testo: str = "", stato: str = "al lavoro", grezzo: int = 2) -> Avanzamento:
    return Avanzamento(task_id="t-1", stato=stato, stato_grezzo=grezzo, testo=testo)


class FakeRemote:
    def __init__(self, pezzi: list[str], ritardo: float = 0.0) -> None:
        self.pezzi = pezzi
        self.ritardo = ritardo
        self.domande: list[str] = []

    async def chiedi(self, testo: str, task_id=None, context_id=None, webhook=None):
        self.domande.append(testo)
        yield avanzamento(stato="accettato", grezzo=1)
        for pezzo in self.pezzi:
            if self.ritardo:
                await asyncio.sleep(self.ritardo)
            yield avanzamento(pezzo)
        yield avanzamento(stato="concluso", grezzo=3)


def tool_with(remote: FakeRemote, streaming: bool = True, loader=None):
    async def load():
        return card(streaming)

    return build_subagent_tools(
        "http://kb:8200/", card_loader=loader or load, client_factory=lambda _card: remote
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
    class Rotto:
        async def chiedi(self, testo, task_id=None, context_id=None, webhook=None):
            raise ConnectionError("knowledge agent giu'")
            yield

    async def load():
        return card()

    strumento = build_subagent_tools(
        "http://kb:8200/", card_loader=load, client_factory=lambda _card: Rotto()
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


class RemoteConArtefatti(FakeRemote):
    async def chiedi(self, testo, task_id=None, context_id=None, webhook=None):
        from demo.a2a.client import Artefatto

        self.domande.append(testo)
        yield avanzamento(stato="accettato", grezzo=1)
        a = avanzamento()
        a.artefatto = Artefatto(
            artifact_id="a1", name="scheda", description="", testo="dal documento"
        )
        yield a
        yield avanzamento(stato="concluso", grezzo=3)


class RemoteCheChiede(FakeRemote):
    async def chiedi(self, testo, task_id=None, context_id=None, webhook=None):
        self.domande.append(testo)
        a = avanzamento(stato="attende una risposta", grezzo=6)
        a.domanda = "Su quale versione di Go?"
        yield a


@pytest.mark.asyncio
async def test_the_text_that_arrives_as_an_artifact_is_not_lost():
    strumento = tool_with(RemoteConArtefatti([]))

    risposta = await strumento.func(domanda="qualcosa")

    assert "dal documento" in risposta.text


@pytest.mark.asyncio
async def test_the_task_lifecycle_ends_up_in_the_logs(caplog):
    strumento = tool_with(FakeRemote(["ok"]))

    with caplog.at_level(logging.INFO, logger="demo.tools.subagent_tools"):
        await strumento.func(domanda="qualcosa")

    assert "accettato -> al lavoro -> concluso" in caplog.text
    assert "task t-1" in caplog.text


@pytest.mark.asyncio
async def test_a_subagent_that_asks_is_reported_not_answered_for():
    strumento = tool_with(RemoteCheChiede([]))

    risposta = await strumento.func(domanda="qualcosa")

    assert "si e' fermato e chiede" in risposta.text
    assert "Su quale versione di Go?" in risposta.text


class RemoteCheRiprende(FakeRemote):
    def __init__(self) -> None:
        super().__init__([])
        self.ripresi: list[str | None] = []

    async def chiedi(self, testo, task_id=None, context_id=None, webhook=None):
        self.ripresi.append(task_id)
        self.domande.append(testo)
        yield avanzamento("Con Go: goroutine e channel.", stato="concluso", grezzo=3)


def strumenti(remote):
    async def load():
        return card()

    return build_subagent_tools(
        "http://kb:8200/", card_loader=load, client_factory=lambda _card: remote
    )


@pytest.mark.asyncio
async def test_an_asking_subagent_is_remembered_in_the_thread_state():
    strumento = tool_with(RemoteCheChiede([]))

    risultato = await strumento.func(domanda="come funziona la concorrenza?")

    stato = risultato.additional_properties["__ag_ui_tool_result_state__"]
    assert stato["subagent_pending"]["task_id"] == "t-1"
    assert "Su quale versione di Go?" in stato["subagent_pending"]["domanda"]


@pytest.mark.asyncio
async def test_the_answer_resumes_the_same_task():
    from demo.server.run_context import current_pending

    remote = RemoteCheRiprende()
    rispondi = strumenti(remote)[1]
    token = current_pending.set({"task_id": "t-99", "agente": "knowledge", "domanda": "quale?"})
    try:
        risultato = await rispondi.func(risposta="Go")
    finally:
        current_pending.reset(token)

    # Lo stesso task, non uno nuovo: e' questo che rende la risposta dell'utente
    # una continuazione e non un'altra conversazione.
    assert remote.ripresi == ["t-99"]
    assert "goroutine" in risultato.text


@pytest.mark.asyncio
async def test_resuming_clears_the_pending_state():
    from demo.server.run_context import current_pending

    rispondi = strumenti(RemoteCheRiprende())[1]
    token = current_pending.set({"task_id": "t-99"})
    try:
        risultato = await rispondi.func(risposta="Go")
    finally:
        current_pending.reset(token)

    assert risultato.additional_properties["__ag_ui_tool_result_state__"]["subagent_pending"] == {}


@pytest.mark.asyncio
async def test_answering_with_nobody_waiting_says_so():
    rispondi = strumenti(RemoteCheRiprende())[1]

    risultato = await rispondi.func(risposta="Go")

    assert "Nessun sottoagente" in risultato.text
