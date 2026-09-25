"""Who said what must survive every exit path, not only the chat UI.

The obligation (AI Act art. 50) is that what the agent produced stays
distinguishable from what the person wrote -- in the thread memory the agent
saves and reloads, in the note a subagent's outcome leaves behind, and in the
operational logs. These are regression tests for paths that already behave:
each one fails the moment a `role` is dropped, renamed, or attached to the
wrong author.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from agent_framework.ag_ui import AGUIThreadSnapshot

from demo.a2a.push import HEADER, token_for
from demo.agents.master import build_master_agent
from demo.chat_clients.fake import FakeStreamingChatClient
from demo.logging_bridge import LogCollector
from demo.memory.remote_store import MemoryServiceSnapshotStore
from demo.server.app import create_app
from demo.server.attachments import annotate_video_audio_attachments

USER_TEXT = "the question typed by the person"
ASSISTANT_TEXT = "the answer written by the agent."


def recording_store() -> tuple[MemoryServiceSnapshotStore, list[dict]]:
    """A real store, wired to a fake memory service that keeps what it is sent."""
    saved: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            saved.append(json.loads(request.content))
            return httpx.Response(200, json={"new_turns": 1})
        return httpx.Response(404, json={"detail": "unknown thread"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://memory"
    )
    return MemoryServiceSnapshotStore("http://memory", client=client), saved


async def run_turn(app, *, thread_id: str, message_id: str, text: str) -> None:
    """One turn as the frontend sends it: only the new message."""
    request = {
        "threadId": thread_id,
        "runId": f"run-{message_id}",
        "state": {},
        "messages": [{"id": message_id, "role": "user", "content": text}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream(
            "POST", "/agui", json=request, headers={"Accept": "text/event-stream"}
        ) as response,
    ):
        assert response.status_code == 200
        async for _ in response.aiter_lines():
            pass


@pytest.mark.asyncio
async def test_the_memory_written_after_a_turn_names_both_authors():
    """The snapshot leaving for the memory service carries a role per message."""
    store, saved = recording_store()
    app = create_app(
        agent=build_master_agent(
            chat_client=FakeStreamingChatClient(chunks=[ASSISTANT_TEXT])
        ),
        snapshot_store=store,
    )

    await run_turn(app, thread_id="t-roles", message_id="m1", text=USER_TEXT)

    assert saved, "nothing was written to the memory service"
    messages = saved[-1]["messages"]
    # Not "some role somewhere": every message says who produced it, and the
    # two texts are attributed to the two different authors.
    assert all(message.get("role") for message in messages)
    author_of = {
        message.get("content"): message.get("role")
        for message in messages
        if isinstance(message.get("content"), str)
    }
    assert author_of[USER_TEXT] == "user"
    assert author_of[ASSISTANT_TEXT] == "assistant"


@pytest.mark.asyncio
async def test_the_memory_read_back_keeps_each_author():
    """And the return trip: reading does not flatten the history into text."""
    remembered = [
        {"id": "m1", "role": "user", "content": USER_TEXT},
        {"id": "m2", "role": "assistant", "content": ASSISTANT_TEXT},
    ]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"messages": remembered, "state": None})
        ),
        base_url="http://memory",
    )
    store = MemoryServiceSnapshotStore("http://memory", client=client)

    snapshot = await store.get(scope="tenant-a", thread_id="t1")

    assert snapshot is not None
    assert [message["role"] for message in snapshot.messages] == ["user", "assistant"]
    assert snapshot.messages == remembered


@pytest.mark.asyncio
async def test_a_subagent_outcome_is_noted_as_the_agent_speaking():
    """The note about a subagent's result is the agent reporting, never the person."""
    received: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(201, json={"seq": 1})

    monkey_target = httpx.AsyncClient

    def fake(*args, **kwargs):
        # Only the client towards memory: patching them all would intercept the
        # ASGI transport this test talks to the app with.
        if kwargs.get("base_url") == "http://memory":
            kwargs["transport"] = httpx.MockTransport(transport)
        return monkey_target(*args, **kwargs)

    app = create_app(
        agent=build_master_agent(chat_client=FakeStreamingChatClient(chunks=["ok"]))
    )
    notification = {
        "task": {
            "id": "task-77",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"name": "briefing", "parts": [{"text": "what it found"}]}],
        }
    }

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DEMO_MEMORY_SERVICE_URL", "http://memory")
        patch.setattr("demo.server.app.httpx.AsyncClient", fake)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/a2a/push/tenant-a/t-note",
                json=notification,
                headers={HEADER: token_for("t-note")},
            )
        await asyncio.sleep(0)

    assert response.status_code == 200
    assert received, "nothing was written to memory"
    assert received[0]["role"] == "assistant"
    assert "what it found" in received[0]["content"]


@pytest.mark.asyncio
async def test_the_operational_logs_do_not_carry_conversation_verbatim():
    """`operational_logs` is counts and states, so nothing lands there unlabelled.

    A log line quoting a turn word for word would be a copy of the
    conversation in a table whose rows have no author at all -- user and
    assistant text side by side, indistinguishable. The lines these paths
    produce say how many messages moved, not what they said.
    """
    store, _ = recording_store()
    snapshot = AGUIThreadSnapshot(
        messages=[
            {"id": "m1", "role": "user", "content": USER_TEXT},
            {"id": "m2", "role": "assistant", "content": ASSISTANT_TEXT},
        ],
        state=None,
    )

    with LogCollector() as collector:
        logging.getLogger("demo").setLevel(logging.INFO)
        await store.save(scope="tenant-a", thread_id="t1", snapshot=snapshot)
        await store.get(scope="tenant-a", thread_id="t1")

    entries = collector.since("")["entries"]
    assert entries, "the memory paths logged nothing at all"
    for entry in entries:
        assert set(entry) >= {"ts", "level", "source", "message"}
        assert USER_TEXT not in entry["message"]
        assert ASSISTANT_TEXT not in entry["message"]


def test_annotating_an_attachment_does_not_change_who_is_speaking():
    """The one place this repo rewrites a message on its way to the model."""
    messages = [
        {
            "id": "m1",
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {
                    "type": "video",
                    "source": {"type": "url", "value": "http://example.test/v.mp4"},
                },
            ],
        },
        {"id": "m2", "role": "assistant", "content": "I am looking."},
    ]

    annotated = annotate_video_audio_attachments(messages)

    assert [message["role"] for message in annotated] == ["user", "assistant"]
    assert annotated[0]["id"] == "m1"
    assert "http://example.test/v.mp4" in annotated[0]["content"][-1]["text"]
