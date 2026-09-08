"""Il servizio nel suo insieme: cache calda, degrado, API."""
from __future__ import annotations

import httpx
import pytest

from memory_service.api import create_app
from memory_service.models import NewMessage, Snapshot
from memory_service.service import ThreadMemory

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]


@pytest.fixture
def memory(transcripts, hot) -> ThreadMemory:
    return ThreadMemory(transcripts, hot)


class BrokenHot:
    """Redis spento. Il servizio deve degradare, non cadere."""

    async def append(self, *args, **kwargs) -> None:
        raise ConnectionError("redis giu'")

    async def tail(self, *args, **kwargs) -> None:
        raise ConnectionError("redis giu'")

    async def forget(self, *args, **kwargs) -> None:
        raise ConnectionError("redis giu'")

    async def ping(self) -> None:
        raise ConnectionError("redis giu'")


async def client_for(memory: ThreadMemory) -> httpx.AsyncClient:
    app = create_app(memory=memory)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_the_tail_comes_from_the_cache_once_it_is_warm(memory, scope):
    for i in range(4):
        await memory.append(scope, "t1", NewMessage(role="user", content=str(i)))

    result = await memory.tail(scope, "t1", limit=3)

    assert result.source == "hot"
    assert [m.content for m in result.messages] == ["1", "2", "3"]


async def test_without_redis_it_still_answers_from_the_durable_store(transcripts, scope):
    degraded = ThreadMemory(transcripts, BrokenHot())

    await degraded.append(scope, "t1", NewMessage(role="user", content="scritto comunque"))
    result = await degraded.tail(scope, "t1", limit=10)

    # Il messaggio e' al sicuro anche se la cache non ha mai visto nulla.
    assert result.source == "durable"
    assert [m.content for m in result.messages] == ["scritto comunque"]


async def test_the_cache_never_holds_the_only_copy(memory, transcripts, hot, scope):
    await memory.append(scope, "t1", NewMessage(role="user", content="unico"))
    await hot.forget(scope, "t1")

    result = await memory.tail(scope, "t1", limit=10)

    assert result.source == "durable"
    assert [m.content for m in result.messages] == ["unico"]


async def test_the_api_writes_and_reads_a_thread(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        created = await client.post(
            "/threads/t1/messages",
            json={"role": "user", "content": "ciao"},
            headers=headers,
        )
        read = await client.get("/threads/t1/messages", headers=headers)

    assert created.status_code == 201
    assert created.json()["seq"] == 1
    assert [m["content"] for m in read.json()["messages"]] == ["ciao"]


async def test_the_api_refuses_a_request_without_scope(memory):
    async with await client_for(memory) as client:
        response = await client.get("/threads/t1/messages")

    # Senza scope non esiste un confine: meglio un errore di una lettura
    # su un ambito indovinato.
    assert response.status_code == 422


async def test_the_api_keeps_scopes_apart(memory, scope):
    async with await client_for(memory) as client:
        await client.post(
            "/threads/t1/messages",
            json={"role": "user", "content": "riservato"},
            headers={"X-Memory-Scope": scope},
        )
        altrui = await client.get(
            "/threads/t1/messages", headers={"X-Memory-Scope": f"{scope}-altro"}
        )

    assert altrui.json()["messages"] == []


async def test_the_api_forgets_a_thread(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.post(
            "/threads/t1/messages", json={"role": "user", "content": "ciao"}, headers=headers
        )
        removed = await client.delete("/threads/t1", headers=headers)
        read = await client.get("/threads/t1/messages", headers=headers)

    assert removed.json()["buckets_rimossi"] == 1
    assert read.json()["messages"] == []


async def test_health_says_ok_when_both_memories_answer(memory):
    async with await client_for(memory) as client:
        response = await client.get("/health")

    assert response.json() == {"status": "ok", "durable": "ok", "hot": "ok"}


async def test_health_says_degraded_when_redis_is_down(transcripts):
    async with await client_for(ThreadMemory(transcripts, BrokenHot())) as client:
        response = await client.get("/health")

    # Degradato, non guasto: i messaggi si scrivono e si leggono lo stesso.
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


async def test_a_snapshot_becomes_turns(memory, scope):
    written = await memory.save_snapshot(
        scope,
        "t1",
        Snapshot(
            messages=[
                {"id": "m1", "role": "user", "content": "ciao"},
                {"id": "m2", "role": "assistant", "content": "ok"},
            ],
            state={"plan": {"status": "idle"}},
        ),
    )

    tail = await memory.tail(scope, "t1", limit=10)
    assert written == 2
    assert [m.content for m in tail.messages] == ["ciao", "ok"]


async def test_resending_the_same_snapshot_writes_nothing(memory, scope):
    snapshot = Snapshot(messages=[{"id": "m1", "role": "user", "content": "ciao"}])

    await memory.save_snapshot(scope, "t1", snapshot)
    again = await memory.save_snapshot(scope, "t1", snapshot)

    # E' cio' che rende sicuro rimandare lo stato completo a ogni run.
    assert again == 0
    assert len((await memory.tail(scope, "t1", limit=10)).messages) == 1


async def test_a_snapshot_grows_by_the_new_turns_only(memory, scope):
    await memory.save_snapshot(
        scope, "t1", Snapshot(messages=[{"id": "m1", "role": "user", "content": "uno"}])
    )
    written = await memory.save_snapshot(
        scope,
        "t1",
        Snapshot(
            messages=[
                {"id": "m1", "role": "user", "content": "uno"},
                {"id": "m2", "role": "assistant", "content": "due"},
            ]
        ),
    )

    assert written == 1


async def test_the_snapshot_comes_back_whole(memory, scope):
    original = Snapshot(
        messages=[
            {"id": "m1", "role": "user", "content": "ciao"},
            {
                "id": "m2",
                "role": "assistant",
                "content": "",
                "toolCalls": [{"id": "c1", "function": {"name": "ui_table"}}],
            },
        ],
        state={"plan": {"status": "in_progress"}},
        session_state={"provider": "continuazione"},
    )
    await memory.save_snapshot(scope, "t1", original)

    rebuilt = await memory.read_snapshot(scope, "t1")

    # Le chiamate ai tool devono tornare identiche: un thread ricostruito a
    # meta' e' una conversazione che al modello non risulta.
    assert rebuilt.messages == original.messages
    assert rebuilt.state == original.state
    assert rebuilt.session_state == original.session_state


async def test_an_unknown_thread_has_no_snapshot(memory, scope):
    assert await memory.read_snapshot(scope, "mai-visto") is None


async def test_the_api_round_trips_a_snapshot(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        saved = await client.put(
            "/threads/t1/snapshot",
            json={"messages": [{"id": "m1", "role": "user", "content": "ciao"}], "state": {"a": 1}},
            headers=headers,
        )
        read = await client.get("/threads/t1/snapshot", headers=headers)

    assert saved.json() == {"turni_nuovi": 1}
    assert read.json()["messages"] == [{"id": "m1", "role": "user", "content": "ciao"}]
    assert read.json()["state"] == {"a": 1}


async def test_the_api_says_404_for_a_thread_it_never_saw(memory, scope):
    async with await client_for(memory) as client:
        response = await client.get("/threads/mai-visto/snapshot", headers={"X-Memory-Scope": scope})

    assert response.status_code == 404


async def test_the_returned_context_is_pruned_but_the_transcript_is_whole(memory, scope):
    await memory.save_snapshot(
        scope,
        "t1",
        Snapshot(
            messages=[
                {"id": "m1", "role": "user", "content": "domanda"},
                {"id": "m2", "role": "reasoning", "content": "", "encrypted_value": "[lungo]"},
                {"id": "m3", "role": "assistant", "content": "risposta"},
            ]
        ),
    )

    potato = await memory.read_snapshot(scope, "t1")
    integrale = await memory.read_snapshot(scope, "t1", raw=True)

    # Conservare tutto e restituire il necessario sono due decisioni diverse.
    assert [m["role"] for m in potato.messages] == ["user", "assistant"]
    assert [m["role"] for m in integrale.messages] == ["user", "reasoning", "assistant"]
    assert potato.curation["ragionamenti_tolti"] == 1
    assert integrale.curation is None


async def test_the_api_can_ask_for_the_whole_transcript(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.put(
            "/threads/t1/snapshot",
            json={
                "messages": [
                    {"id": "m1", "role": "user", "content": "domanda"},
                    {"id": "m2", "role": "reasoning", "content": "pensiero"},
                ]
            },
            headers=headers,
        )
        potato = await client.get("/threads/t1/snapshot", headers=headers)
        integrale = await client.get("/threads/t1/snapshot?raw=true", headers=headers)

    assert [m["role"] for m in potato.json()["messages"]] == ["user"]
    assert [m["role"] for m in integrale.json()["messages"]] == ["user", "reasoning"]


class RecordingSummarizer:
    """Riassuntore finto: registra cosa gli e' stato dato da riassumere."""

    _model = "finto"

    def __init__(self, text: str = "si parlava del codice ORCHIDEA-77") -> None:
        self.text = text
        self.calls: list[list[dict]] = []

    async def summarize(self, messages):
        self.calls.append(messages)
        return self.text


class BrokenSummarizer:
    _model = "rotto"

    async def summarize(self, messages):
        raise RuntimeError("modello irraggiungibile")


def long_thread(turns: int) -> list[dict]:
    messages = []
    for i in range(turns):
        messages.append({"id": f"u{i}", "role": "user", "content": f"domanda {i}"})
        messages.append({"id": f"a{i}", "role": "assistant", "content": f"risposta {i}"})
    return messages


@pytest.fixture
def summarizer() -> RecordingSummarizer:
    return RecordingSummarizer()


@pytest.fixture
def compacting(transcripts, hot, summarizer) -> ThreadMemory:
    from memory_service.curation import ContextPolicy

    return ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), summarizer)


async def test_a_short_thread_is_not_summarized(compacting, summarizer, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(2)))
    await compacting.compact_if_needed(scope, "t1")

    # Nulla esce dalla finestra: riassumere sarebbe spesa senza guadagno.
    assert summarizer.calls == []


async def test_crossing_the_window_produces_a_summary(compacting, summarizer, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    assert len(summarizer.calls) == 1
    # Riassume esattamente cio' che la lettura lascera' fuori.
    assert [m["content"] for m in summarizer.calls[0]][:2] == ["domanda 0", "risposta 0"]


async def test_the_summary_arrives_at_the_head_of_the_context(compacting, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    snapshot = await compacting.read_snapshot(scope, "t1")

    assert snapshot.messages[0]["role"] == "system"
    assert "ORCHIDEA-77" in snapshot.messages[0]["content"]
    assert snapshot.curation["riassunti"] == 1
    assert snapshot.curation["messaggi_scartati"] > 0


async def test_the_whole_transcript_has_no_summary_in_it(compacting, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    integrale = await compacting.read_snapshot(scope, "t1", raw=True)

    # Il riassunto e' una ricomposizione, non un turno: nel transcript non c'e'.
    assert all(m.get("role") != "system" for m in integrale.messages)


async def test_the_summary_does_not_get_rewritten_at_every_run(compacting, summarizer, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    # Stesso taglio, stesso riassunto: rifarlo sarebbe un'inferenza a vuoto.
    assert len(summarizer.calls) == 1


async def test_the_summary_returned_does_not_become_a_turn(compacting, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")
    context = await compacting.read_snapshot(scope, "t1")

    # Il client rimanda indietro cio' che ha ricevuto, riassunto compreso.
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=context.messages))
    integrale = await compacting.read_snapshot(scope, "t1", raw=True)

    assert all(m.get("role") != "system" for m in integrale.messages)


async def test_a_broken_summarizer_does_not_break_the_conversation(transcripts, hot, scope, caplog):
    import logging

    from memory_service.curation import ContextPolicy

    memory = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), BrokenSummarizer())

    with caplog.at_level(logging.ERROR):
        await memory.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
        await memory.compact_if_needed(scope, "t1")
    snapshot = await memory.read_snapshot(scope, "t1")

    # Si continua piu' smemorati, e il log lo dice: quei turni sono usciti
    # senza lasciare un riassunto al loro posto.
    assert snapshot.curation["riassunti"] == 0
    assert "Riassunto NON prodotto" in caplog.text


async def test_without_a_summarizer_nothing_is_compacted(transcripts, hot, scope):
    from memory_service.curation import ContextPolicy

    memory = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6))

    await memory.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await memory.compact_if_needed(scope, "t1")
    snapshot = await memory.read_snapshot(scope, "t1")

    assert snapshot.curation["riassunti"] == 0


async def test_forgetting_a_thread_takes_its_summaries_too(compacting, transcripts, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    await compacting.forget(scope, "t1")

    # Un riassunto sopravvissuto racconterebbe una conversazione cancellata.
    assert await transcripts.latest_summary(scope, "t1") is None


class SlowSummarizer:
    """Un modello lento. E' il caso normale, non quello patologico."""

    _model = "lento"

    def __init__(self) -> None:
        self.called = False

    async def summarize(self, messages):
        import asyncio

        self.called = True
        await asyncio.sleep(5)
        return "riassunto tardivo"


async def test_saving_does_not_wait_for_the_summary(transcripts, hot, scope):
    import asyncio

    from memory_service.curation import ContextPolicy

    slow = SlowSummarizer()
    memory = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), slow)

    # Misurato sul campo: con il riassunto dentro la PUT, il client dell'agente
    # va in timeout e la memoria di quel turno si perde.
    async with asyncio.timeout(3):
        await memory.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))

    assert slow.called is False


async def test_the_api_compacts_after_answering(transcripts, hot, summarizer, scope):
    from memory_service.curation import ContextPolicy

    memory = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), summarizer)

    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        response = await client.put(
            "/threads/t1/snapshot", json={"messages": long_thread(6)}, headers=headers
        )
        context = await client.get("/threads/t1/snapshot", headers=headers)

    # Che il riassunto arrivi *dopo* la risposta qui non si vede: il transport
    # ASGI dei test aspetta anche i task di sfondo prima di restituire. Lo
    # garantisce il test sopra, sul servizio; qui si verifica che il giro
    # completo -- salva, compatta, ricomponi -- funzioni davvero.
    assert response.status_code == 200
    assert context.json()["curation"]["riassunti"] == 1
