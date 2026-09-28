"""Thread memory: the history is owned by the server, not by the client.

The AG-UI client sends only the new turn. Without server-side memory every run
starts from scratch, and the model does not know what was said a moment ago.
"""
import json
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import httpx
import pytest
from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

from master_agent.agents.master import build_master_agent
from master_agent.server.app import create_app


class RecordingChatClient(BaseChatClient):
    """Records the messages received on each call, then answers a fixed text."""

    def __init__(self, reply: str = "ok") -> None:
        super().__init__()
        self._reply = reply
        self.seen: list[list[Message]] = []

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self.seen.append(list(messages))

        if not stream:

            async def _once() -> ChatResponse:
                return ChatResponse(
                    messages=[
                        Message(
                            role="assistant", contents=[Content.from_text(self._reply)]
                        )
                    ]
                )

            return _once()

        async def _stream():
            yield ChatResponseUpdate(
                contents=[Content.from_text(self._reply)], role="assistant"
            )

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)

def user_texts(messages: Sequence[Message]) -> list[str]:
    return [m.text for m in messages if m.role == "user"]

def all_texts(messages: Sequence[Message]) -> str:
    return "\n".join(m.text or "" for m in messages)

async def run_turn(app, *, thread_id: str, message_id: str, text: str) -> list[dict]:
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
        return [
            json.loads(line[len("data: "):])
            async for line in response.aiter_lines()
            if line.startswith("data: ")
        ]

@pytest.fixture
def recording() -> RecordingChatClient:
    return RecordingChatClient()

@pytest.fixture
def memory_app(recording: RecordingChatClient):
    return create_app(agent=build_master_agent(chat_client=recording))

@pytest.mark.asyncio
async def test_second_turn_sees_the_first(memory_app, recording):
    await run_turn(
        memory_app, thread_id="t1", message_id="m1", text="remember the number 4271"
    )
    await run_turn(memory_app, thread_id="t1", message_id="m2", text="which number?")

    second_turn = recording.seen[-1]
    assert user_texts(second_turn) == ["remember the number 4271", "which number?"]

@pytest.mark.asyncio
async def test_the_answer_given_stays_in_the_history(memory_app, recording):
    await run_turn(memory_app, thread_id="t1", message_id="m1", text="hello")
    await run_turn(memory_app, thread_id="t1", message_id="m2", text="again")

    assert "ok" in all_texts(recording.seen[-1])

@pytest.mark.asyncio
async def test_threads_do_not_leak_into_each_other(memory_app, recording):
    await run_turn(memory_app, thread_id="t1", message_id="m1", text="secret of t1")
    await run_turn(memory_app, thread_id="t2", message_id="m2", text="this is t2")

    assert user_texts(recording.seen[-1]) == ["this is t2"]

@pytest.mark.asyncio
async def test_the_first_turn_carries_only_the_new_message(memory_app, recording):
    await run_turn(memory_app, thread_id="fresh", message_id="m1", text="first")

    assert user_texts(recording.seen[-1]) == ["first"]

@pytest.mark.asyncio
async def test_history_survives_a_third_turn(memory_app, recording):
    for i, text in enumerate(["one", "two", "three"], start=1):
        await run_turn(memory_app, thread_id="long", message_id=f"m{i}", text=text)

    assert user_texts(recording.seen[-1]) == ["one", "two", "three"]
