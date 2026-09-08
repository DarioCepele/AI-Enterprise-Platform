"""L'executor: ciclo di vita del task e artefatto strutturato."""
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

from knowledge.executor import ARTEFATTO, KnowledgeExecutor, scheda


class Update:
    def __init__(self, text: str = "", contents: list | None = None) -> None:
        self.text = text
        self.contents = contents or []


def lettura(nome: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name="leggi_documento", arguments={"nome": nome})


class FakeAgent:
    def __init__(self, updates: list[Update] | None = None, errore: Exception | None = None) -> None:
        self._updates = updates or []
        self._errore = errore
        self.domande: list[str] = []

    def run(self, domanda: str, stream: bool = False):
        self.domande.append(domanda)
        errore = self._errore
        updates = self._updates

        async def _stream():
            for update in updates:
                yield update
            if errore:
                raise errore

        return _stream()


class FakeContext:
    task_id = "task-1"
    context_id = "ctx-1"
    current_task = None

    def __init__(self, domanda: str) -> None:
        self._domanda = domanda
        self.message = Message(
            message_id="m-1", role=Role.ROLE_USER, parts=[Part(text=domanda)]
        )

    def get_user_input(self) -> str:
        return self._domanda


class CodaFinta:
    """Raccoglie gli eventi invece di consegnarli: e' quello che si verifica."""

    def __init__(self) -> None:
        self.eventi: list = []

    async def enqueue_event(self, event) -> None:
        self.eventi.append(event)


async def esegui(agent: FakeAgent, domanda: str = "come tipizza Go?") -> list:
    coda = CodaFinta()
    await KnowledgeExecutor(agent).execute(FakeContext(domanda), coda)
    return coda.eventi


def stati(eventi: list) -> list[int]:
    return [e.status.state for e in eventi if isinstance(e, TaskStatusUpdateEvent)]


def artefatti(eventi: list) -> list:
    return [e.artifact for e in eventi if isinstance(e, TaskArtifactUpdateEvent)]


@pytest.mark.asyncio
async def test_the_task_goes_through_its_states():
    eventi = await esegui(FakeAgent([Update("Go usa valori di errore.")]))

    percorso = stati(eventi)
    assert percorso[0] == TaskState.TASK_STATE_SUBMITTED
    assert TaskState.TASK_STATE_WORKING in percorso
    assert percorso[-1] == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_the_answer_arrives_as_a_named_artifact_with_data():
    eventi = await esegui(
        FakeAgent([Update("", [lettura("go")]), Update("Go usa valori di errore.")])
    )

    prodotti = artefatti(eventi)
    assert len(prodotti) == 1
    artefatto = prodotti[0]
    assert artefatto.name == ARTEFATTO
    dati = MessageToDict(next(p.data for p in artefatto.parts if p.HasField("data")))
    assert dati["documenti"] == ["go"]
    assert dati["estratto"] == "Go usa valori di errore."


@pytest.mark.asyncio
async def test_the_artifact_also_carries_plain_text_for_the_model():
    eventi = await esegui(FakeAgent([Update("Risposta.")]))

    artefatto = artefatti(eventi)[0]
    assert any(part.text == "Risposta." for part in artefatto.parts)


@pytest.mark.asyncio
async def test_the_same_document_read_twice_is_listed_once():
    eventi = await esegui(
        FakeAgent([Update("", [lettura("go"), lettura("go")]), Update("Risposta.")])
    )

    artefatto = artefatti(eventi)[0]
    dati = MessageToDict(next(p.data for p in artefatto.parts if p.HasField("data")))
    assert dati["documenti"] == ["go"]


@pytest.mark.asyncio
async def test_the_text_streams_while_the_task_works():
    eventi = await esegui(FakeAgent([Update("primo "), Update("secondo")]))

    parlati = [
        "".join(part.text for part in e.status.message.parts)
        for e in eventi
        if isinstance(e, TaskStatusUpdateEvent) and e.status.message.parts
    ]
    assert parlati == ["primo ", "secondo"]


@pytest.mark.asyncio
async def test_a_failing_agent_fails_the_task_instead_of_hanging():
    eventi = await esegui(FakeAgent([Update("a meta")], errore=RuntimeError("modello giu'")))

    assert stati(eventi)[-1] == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_an_empty_answer_fails_the_task_instead_of_completing_it():
    eventi = await esegui(FakeAgent([]))

    assert stati(eventi)[-1] == TaskState.TASK_STATE_FAILED
    assert artefatti(eventi) == []


def test_the_card_shape_is_documented_by_the_helper():
    parti = scheda("domanda", "risposta", ["go", "rust"])

    dati = MessageToDict(next(p.data for p in parti if p.HasField("data")))
    assert dati["component"] == "scheda"
    assert dati["documenti"] == ["go", "rust"]


def lettura_delta(call_id: str, pezzo: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", name="leggi_documento", call_id=call_id, arguments=pezzo
    )


@pytest.mark.asyncio
async def test_arguments_that_arrive_in_pieces_are_still_understood():
    eventi = await esegui(
        FakeAgent(
            [
                Update("", [lettura_delta("c1", "")]),
                Update("", [lettura_delta("c1", '{"nome"')]),
                Update("", [lettura_delta("c1", ': "rust"}')]),
                Update("Risposta."),
            ]
        )
    )

    dati = MessageToDict(
        next(p.data for p in artefatti(eventi)[0].parts if p.HasField("data"))
    )
    assert dati["documenti"] == ["rust"]


@pytest.mark.asyncio
async def test_two_documents_read_in_one_run_are_both_listed():
    eventi = await esegui(
        FakeAgent(
            [
                Update("", [lettura_delta("c1", '{"nome": "go"}')]),
                Update("", [lettura_delta("c2", '{"nome": "rust"}')]),
                Update("Risposta."),
            ]
        )
    )

    dati = MessageToDict(
        next(p.data for p in artefatti(eventi)[0].parts if p.HasField("data"))
    )
    assert dati["documenti"] == ["go", "rust"]


@pytest.mark.asyncio
async def test_the_name_arrives_only_on_the_first_piece():
    eventi = await esegui(
        FakeAgent(
            [
                Update("", [SimpleNamespace(type="function_call", name="leggi_documento", call_id="c1", arguments="")]),
                Update("", [SimpleNamespace(type="function_call", name="", call_id="c1", arguments='{"nome": "go"}')]),
                Update("Risposta."),
            ]
        )
    )

    dati = MessageToDict(next(p.data for p in artefatti(eventi)[0].parts if p.HasField("data")))
    assert dati["documenti"] == ["go"]


@pytest.mark.asyncio
async def test_another_tools_arguments_are_not_mistaken_for_ours():
    eventi = await esegui(
        FakeAgent(
            [
                Update("", [SimpleNamespace(type="function_call", name="altro_tool", call_id="c9", arguments="")]),
                Update("", [SimpleNamespace(type="function_call", name="", call_id="c9", arguments='{"nome": "non-nostro"}')]),
                Update("Risposta."),
            ]
        )
    )

    dati = MessageToDict(next(p.data for p in artefatti(eventi)[0].parts if p.HasField("data")))
    assert dati.get("documenti", []) == []


@pytest.mark.asyncio
async def test_an_ambiguous_question_leaves_the_task_waiting_for_input():
    eventi = await esegui(
        FakeAgent([Update("[SERVE-CHIARIMENTO] Di quale linguaggio parli?")]),
        domanda="come funziona la concorrenza?",
    )

    # Non completato e non fallito: il task resta aperto, in attesa che qualcuno
    # risponda. E' l'aggancio dell'human-in-the-loop fra agenti.
    assert stati(eventi)[-1] == TaskState.TASK_STATE_INPUT_REQUIRED
    assert artefatti(eventi) == []


@pytest.mark.asyncio
async def test_the_question_travels_with_the_state():
    eventi = await esegui(FakeAgent([Update("[SERVE-CHIARIMENTO] Di quale linguaggio parli?")]))

    ultimo = [e for e in eventi if isinstance(e, TaskStatusUpdateEvent)][-1]
    testo = "".join(part.text for part in ultimo.status.message.parts)
    assert testo == "Di quale linguaggio parli?"


@pytest.mark.asyncio
async def test_a_marker_without_a_question_still_asks_something():
    eventi = await esegui(FakeAgent([Update("[SERVE-CHIARIMENTO]")]))

    ultimo = [e for e in eventi if isinstance(e, TaskStatusUpdateEvent)][-1]
    assert "precisare" in "".join(part.text for part in ultimo.status.message.parts)
