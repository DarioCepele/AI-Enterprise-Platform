"""Service integration tests covering cache hits, degradation, and HTTP APIs."""
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
    """Simulate unavailable Redis; the service must degrade gracefully."""

    async def append(self, *args, **kwargs) -> None:
        raise ConnectionError("redis down")

    async def tail(self, *args, **kwargs) -> None:
        raise ConnectionError("redis down")

    async def forget(self, *args, **kwargs) -> None:
        raise ConnectionError("redis down")

    async def ping(self) -> None:
        raise ConnectionError("redis down")


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

    await degraded.append(scope, "t1", NewMessage(role="user", content="written anyway"))
    result = await degraded.tail(scope, "t1", limit=10)

    assert result.source == "durable"
    assert [m.content for m in result.messages] == ["written anyway"]


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

    assert response.status_code == 422


async def test_the_api_keeps_scopes_apart(memory, scope):
    async with await client_for(memory) as client:
        await client.post(
            "/threads/t1/messages",
            json={"role": "user", "content": "riservato"},
            headers={"X-Memory-Scope": scope},
        )
        altrui = await client.get(
            "/threads/t1/messages", headers={"X-Memory-Scope": f"{scope}-other"}
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

    assert removed.json()["buckets_removed"] == 1
    assert read.json()["messages"] == []


async def test_health_says_ok_when_both_memories_answer(memory):
    async with await client_for(memory) as client:
        response = await client.get("/health")

    assert response.json() == {"status": "ok", "durable": "ok", "hot": "ok"}


async def test_health_says_degraded_when_redis_is_down(transcripts):
    async with await client_for(ThreadMemory(transcripts, BrokenHot())) as client:
        response = await client.get("/health")

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

    assert saved.json() == {"new_turns": 1}
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
                {"id": "m1", "role": "user", "content": "question"},
                {"id": "m2", "role": "reasoning", "content": "", "encrypted_value": "[lungo]"},
                {"id": "m3", "role": "assistant", "content": "answer"},
            ]
        ),
    )

    pruned = await memory.read_snapshot(scope, "t1")
    integrale = await memory.read_snapshot(scope, "t1", raw=True)

    assert [m["role"] for m in pruned.messages] == ["user", "assistant"]
    assert [m["role"] for m in integrale.messages] == ["user", "reasoning", "assistant"]
    assert pruned.curation["reasoning_removed"] == 1
    assert integrale.curation is None


async def test_the_api_can_ask_for_the_whole_transcript(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.put(
            "/threads/t1/snapshot",
            json={
                "messages": [
                    {"id": "m1", "role": "user", "content": "question"},
                    {"id": "m2", "role": "reasoning", "content": "pensiero"},
                ]
            },
            headers=headers,
        )
        pruned = await client.get("/threads/t1/snapshot", headers=headers)
        integrale = await client.get("/threads/t1/snapshot?raw=true", headers=headers)

    assert [m["role"] for m in pruned.json()["messages"]] == ["user"]
    assert [m["role"] for m in integrale.json()["messages"]] == ["user", "reasoning"]


class RecordingSummarizer:
    """Record the messages supplied to a fake summarizer."""

    _model = "finto"

    def __init__(self, text: str = "the code ORCHIDEA-77 was mentioned") -> None:
        self.text = text
        self.calls: list[list[dict]] = []

    async def summarize(self, messages):
        self.calls.append(messages)
        return self.text


class BrokenSummarizer:
    _model = "rotto"

    async def summarize(self, messages):
        raise RuntimeError("model unreachable")


def long_thread(turns: int) -> list[dict]:
    messages = []
    for i in range(turns):
        messages.append({"id": f"u{i}", "role": "user", "content": f"question {i}"})
        messages.append({"id": f"a{i}", "role": "assistant", "content": f"answer {i}"})
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

    assert summarizer.calls == []


async def test_crossing_the_window_produces_a_summary(compacting, summarizer, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    assert len(summarizer.calls) == 1
    assert [m["content"] for m in summarizer.calls[0]][:2] == ["question 0", "answer 0"]


async def test_the_summary_arrives_at_the_head_of_the_context(compacting, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    snapshot = await compacting.read_snapshot(scope, "t1")

    assert snapshot.messages[0]["role"] == "system"
    assert "ORCHIDEA-77" in snapshot.messages[0]["content"]
    assert snapshot.curation["summarized"] == 1
    assert snapshot.curation["messages_dropped"] > 0


async def test_the_whole_transcript_has_no_summary_in_it(compacting, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    integrale = await compacting.read_snapshot(scope, "t1", raw=True)

    assert all(m.get("role") != "system" for m in integrale.messages)


async def test_the_summary_does_not_get_rewritten_at_every_run(compacting, summarizer, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    assert len(summarizer.calls) == 1


async def test_the_summary_returned_does_not_become_a_turn(compacting, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")
    context = await compacting.read_snapshot(scope, "t1")

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

    assert snapshot.curation["summarized"] == 0
    assert "Summary NOT produced" in caplog.text


async def test_without_a_summarizer_nothing_is_compacted(transcripts, hot, scope):
    from memory_service.curation import ContextPolicy

    memory = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6))

    await memory.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await memory.compact_if_needed(scope, "t1")
    snapshot = await memory.read_snapshot(scope, "t1")

    assert snapshot.curation["summarized"] == 0


async def test_forgetting_a_thread_takes_its_summaries_too(compacting, transcripts, scope):
    await compacting.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await compacting.compact_if_needed(scope, "t1")

    await compacting.forget(scope, "t1")

    assert await transcripts.latest_summary(scope, "t1") is None


class SlowSummarizer:
    """Simulate normal model latency."""

    _model = "lento"

    def __init__(self) -> None:
        self.called = False

    async def summarize(self, messages):
        import asyncio

        self.called = True
        await asyncio.sleep(5)
        return "late summary"


async def test_saving_does_not_wait_for_the_summary(transcripts, hot, scope):
    import asyncio

    from memory_service.curation import ContextPolicy

    slow = SlowSummarizer()
    memory = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), slow)

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

    assert response.status_code == 200
    assert context.json()["curation"]["summarized"] == 1


class RecordingExtractor:
    """Record input and return a configured JSON response."""

    def __init__(self, raw: str = '[{"key": "contact", "value": "Marta"}]') -> None:
        self.raw = raw
        self.calls: list[list[dict]] = []

    async def extract_facts(self, messages):
        self.calls.append(messages)
        return self.raw


@pytest.fixture
def extractor() -> RecordingExtractor:
    return RecordingExtractor()


@pytest.fixture
def learning(transcripts, hot, summarizer, extractor) -> ThreadMemory:
    from memory_service.curation import ContextPolicy

    return ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), summarizer, extractor)


async def test_facts_are_learned_from_the_turns_that_leave(learning, extractor, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    assert len(extractor.calls) == 1
    snapshot = await learning.read_snapshot(scope, "t1")
    assert snapshot.curation["facts"] == 1


async def test_a_fact_learned_in_one_thread_shows_up_in_another(learning, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    await learning.save_snapshot(
        scope, "t2", Snapshot(messages=[{"id": "x", "role": "user", "content": "ciao"}])
    )
    other = await learning.read_snapshot(scope, "t2")

    assert other.messages[0]["role"] == "system"
    assert "contact: Marta" in other.messages[0]["content"]


async def test_facts_do_not_cross_scopes(learning, transcripts, hot, summarizer, extractor, scope):
    from memory_service.curation import ContextPolicy

    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    altrui = ThreadMemory(transcripts, hot, ContextPolicy(max_messages=6), summarizer, extractor)
    await altrui.save_snapshot(
        f"{scope}-other", "t1", Snapshot(messages=[{"id": "y", "role": "user", "content": "ciao"}])
    )
    snapshot = await altrui.read_snapshot(f"{scope}-other", "t1")

    assert snapshot.curation["facts"] == 0


async def test_the_same_fact_updated_does_not_become_two(learning, extractor, transcripts, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    extractor.raw = '[{"key": "contact", "value": "Giulio"}]'
    await learning.save_snapshot(scope, "t2", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t2")

    facts = await transcripts.facts_of(scope, limit=10)
    assert facts == [{"key": "contact", "value": "Giulio"}]


async def test_deleting_a_thread_keeps_the_facts(learning, transcripts, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    await learning.forget(scope, "t1")

    assert await transcripts.facts_of(scope, limit=10) != []


async def test_deleting_the_scope_takes_the_facts_too(learning, transcripts, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    await learning.forget_scope(scope)

    assert await transcripts.facts_of(scope, limit=10) == []


async def test_unreadable_facts_do_not_break_the_compaction(
    transcripts, hot, summarizer, scope, caplog
):
    from memory_service.curation import ContextPolicy

    memory = ThreadMemory(
        transcripts, hot, ContextPolicy(max_messages=6), summarizer, RecordingExtractor("not JSON")
    )
    await memory.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await memory.compact_if_needed(scope, "t1")

    snapshot = await memory.read_snapshot(scope, "t1")
    assert snapshot.curation["facts"] == 0
    assert snapshot.curation["summarized"] == 1


async def test_the_injected_facts_do_not_come_back_as_a_turn(learning, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")
    context = await learning.read_snapshot(scope, "t1")

    await learning.save_snapshot(scope, "t1", Snapshot(messages=context.messages))
    integrale = await learning.read_snapshot(scope, "t1", raw=True)

    assert all(m.get("role") != "system" for m in integrale.messages)


async def test_a_brand_new_thread_still_gets_the_facts(learning, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    brand_new = await learning.read_snapshot(scope, "mai-aperto-prima")

    assert brand_new is not None
    assert brand_new.curation["facts"] == 1
    assert "contact: Marta" in brand_new.messages[0]["content"]


async def test_a_brand_new_thread_without_facts_is_still_unknown(memory, scope):
    assert await memory.read_snapshot(scope, "mai-aperto-prima") is None


async def test_the_whole_transcript_of_an_unknown_thread_stays_unknown(learning, scope):
    await learning.save_snapshot(scope, "t1", Snapshot(messages=long_thread(6)))
    await learning.compact_if_needed(scope, "t1")

    assert await learning.read_snapshot(scope, "mai-aperto-prima", raw=True) is None


class WordEmbedder:
    """Deterministic test embeddings with one axis per keyword. Verify indexing, retrieval, and deletion; real-model checks cover semantic quality."""

    WORDS = ("go", "python", "carbonara")

    @property
    def dimensions(self) -> int:
        return len(self.WORDS)

    async def embed(self, texts):
        return [
            [1.0 if word in text.lower() else 0.0 for word in self.WORDS] for text in texts
        ]


@pytest.fixture
def searchable(transcripts, hot, summarizer, extractor, redis_client, scope):
    from memory_service.curation import ContextPolicy
    from memory_service.stores.vectors import RedisMemories

    memories = RedisMemories(redis_client)
    yield ThreadMemory(
        transcripts,
        hot,
        ContextPolicy(max_messages=6),
        summarizer,
        extractor,
        30,
        WordEmbedder(),
        memories,
    )


def thread_about(*topics: str) -> list[dict]:
    messages = []
    for i, topic in enumerate(topics):
        messages.append({"id": f"u{i}", "role": "user", "content": f"let us talk about {topic}"})
        messages.append({"id": f"a{i}", "role": "assistant", "content": f"ecco su {topic}"})
    return messages


async def test_what_leaves_the_window_becomes_searchable(searchable, scope):
    await searchable.save_snapshot(
        scope, "t1", Snapshot(messages=thread_about("go", "python", "carbonara", "go", "go", "go"))
    )
    await searchable.compact_if_needed(scope, "t1")

    found = await searchable.search_memories(scope, "carbonara", limit=3)

    assert found
    assert "carbonara" in found[0].text


async def test_a_memory_says_which_thread_it_came_from(searchable, scope):
    await searchable.save_snapshot(
        scope, "t1", Snapshot(messages=thread_about("go", "python", "carbonara", "go", "go", "go"))
    )
    await searchable.compact_if_needed(scope, "t1")

    found_one = (await searchable.search_memories(scope, "python", limit=1))[0]

    assert found_one.thread_id == "t1"
    assert found_one.seq > 0


async def test_nothing_is_indexed_twice(searchable, transcripts, redis_client, scope):
    from memory_service.stores.vectors import RedisMemories

    messages = thread_about("go", "python", "carbonara", "go", "go", "go")
    await searchable.save_snapshot(scope, "t1", Snapshot(messages=messages))
    await searchable.compact_if_needed(scope, "t1")
    prima = await RedisMemories(redis_client).count(scope)

    await searchable.compact_if_needed(scope, "t1")

    assert await RedisMemories(redis_client).count(scope) == prima


async def test_forgetting_a_thread_makes_its_memories_unsearchable(searchable, scope):
    await searchable.save_snapshot(
        scope, "t1", Snapshot(messages=thread_about("go", "python", "carbonara", "go", "go", "go"))
    )
    await searchable.compact_if_needed(scope, "t1")

    await searchable.forget(scope, "t1")

    assert await searchable.search_memories(scope, "carbonara", limit=3) == []


async def test_searching_without_an_embedder_returns_nothing_and_says_so(memory, scope, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert await memory.search_memories(scope, "anything at all", limit=3) == []

    assert "not configured" in caplog.text
