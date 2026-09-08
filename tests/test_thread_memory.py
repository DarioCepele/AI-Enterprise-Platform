"""Memoria del thread: la storia la possiede il server, non il client.

Il client AG-UI manda solo il turno nuovo. Senza memoria lato server ogni run
riparte da zero, e il modello non sa cosa e' stato detto un momento prima.
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

from demo.agents.master import build_master_agent
from demo.server.app import create_app


class RecordingChatClient(BaseChatClient):
    """Registra i messaggi ricevuti a ogni chiamata, poi risponde un testo fisso."""

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
                    messages=[Message(role="assistant", contents=[Content.from_text(self._reply)])]
                )

            return _once()

        async def _stream():
            yield ChatResponseUpdate(contents=[Content.from_text(self._reply)], role="assistant")

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)


def user_texts(messages: Sequence[Message]) -> list[str]:
    return [m.text for m in messages if m.role == "user"]


def all_texts(messages: Sequence[Message]) -> str:
    return "\n".join(m.text or "" for m in messages)


async def run_turn(app, *, thread_id: str, message_id: str, text: str) -> list[dict]:
    """Un turno come lo manda il frontend: solo il messaggio nuovo."""
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
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/agui", json=request, headers={"Accept": "text/event-stream"}
        ) as response:
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
    await run_turn(memory_app, thread_id="t1", message_id="m1", text="ricorda il numero 4271")
    await run_turn(memory_app, thread_id="t1", message_id="m2", text="che numero?")

    second_turn = recording.seen[-1]
    assert user_texts(second_turn) == ["ricorda il numero 4271", "che numero?"]


@pytest.mark.asyncio
async def test_the_answer_given_stays_in_the_history(memory_app, recording):
    await run_turn(memory_app, thread_id="t1", message_id="m1", text="ciao")
    await run_turn(memory_app, thread_id="t1", message_id="m2", text="ancora")

    # Senza la risposta dell'assistente il modello rilegge solo le proprie domande.
    assert "ok" in all_texts(recording.seen[-1])


@pytest.mark.asyncio
async def test_threads_do_not_leak_into_each_other(memory_app, recording):
    await run_turn(memory_app, thread_id="t1", message_id="m1", text="segreto di t1")
    await run_turn(memory_app, thread_id="t2", message_id="m2", text="qui e' t2")

    assert user_texts(recording.seen[-1]) == ["qui e' t2"]


@pytest.mark.asyncio
async def test_the_first_turn_carries_only_the_new_message(memory_app, recording):
    await run_turn(memory_app, thread_id="fresco", message_id="m1", text="primo")

    assert user_texts(recording.seen[-1]) == ["primo"]


@pytest.mark.asyncio
async def test_history_survives_a_third_turn(memory_app, recording):
    for i, text in enumerate(["uno", "due", "tre"], start=1):
        await run_turn(memory_app, thread_id="lungo", message_id=f"m{i}", text=text)

    assert user_texts(recording.seen[-1]) == ["uno", "due", "tre"]
