"""A video/audio attachment's URL must be readable as plain text by the model.

The AG-UI adapter turns a ``VideoInputPart``/``AudioInputPart`` with a
``InputContentUrlSource`` into raw ``agent_framework.Content`` (a
``video/*``/``audio/*`` media item) attached to the user message. Whether the
chat client underneath knows what to do with that raw media type is not
guaranteed - so ``LabRunner`` also appends the URL as an ordinary text note,
which every model can read (and later pass back as a tool argument).
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest
from agent_framework import Message

from master_agent.agents.master import build_master_agent
from master_agent.chat_clients.fake import FakeStreamingChatClient
from master_agent.server.app import create_app
from master_agent.server.attachments import annotate_video_audio_attachments

VIDEO_URL = "http://example.test/video.mp4"
AUDIO_URL = "http://example.test/clip.mp3"


class RecordingChatClient(FakeStreamingChatClient):
    """Wraps the project's fake chat client, but also records what it sees.

    The rest of the pipeline (agent, middleware, AG-UI adapter) runs exactly
    as it would with the real fake client; this subclass only adds a hook so
    the test can inspect the messages the "model" actually received.
    """

    def __init__(self, seen: list[list[Message]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._seen = seen

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        self._seen.append(list(messages))
        return super()._inner_get_response(
            messages=messages, stream=stream, options=options, **kwargs
        )


def _all_text(messages: list[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        for content in message.contents or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


async def _run(request: dict[str, Any], seen: list[list[Message]]) -> None:
    app = create_app(agent=build_master_agent(chat_client=RecordingChatClient(seen)))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream(
            "POST", "/agui", json=request, headers={"Accept": "text/event-stream"}
        ) as response,
    ):
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                json.loads(line[len("data: "):])


def _request(thread_id: str, content: Any) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "runId": f"r-{thread_id}",
        "state": {},
        "messages": [{"id": f"m-{thread_id}", "role": "user", "content": content}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


@pytest.mark.asyncio
async def test_a_video_attachment_url_is_visible_as_text_to_the_model():
    seen: list[list[Message]] = []
    content = [
        {"type": "text", "text": "look at this"},
        {
            "type": "video",
            "source": {"type": "url", "value": VIDEO_URL, "mimeType": "video/mp4"},
        },
    ]

    await _run(_request("t-video", content), seen)

    assert seen, "the chat client never received any messages"
    assert VIDEO_URL in _all_text(seen[0])


@pytest.mark.asyncio
async def test_an_audio_attachment_url_is_visible_as_text_to_the_model():
    seen: list[list[Message]] = []
    content = [
        {
            "type": "audio",
            "source": {"type": "url", "value": AUDIO_URL, "mimeType": "audio/mpeg"},
        },
    ]

    await _run(_request("t-audio", content), seen)

    assert seen
    assert AUDIO_URL in _all_text(seen[0])


@pytest.mark.asyncio
async def test_a_plain_text_turn_is_unaffected():
    seen: list[list[Message]] = []

    await _run(_request("t-plain", "just text, no attachment"), seen)

    assert seen
    assert "just text, no attachment" in _all_text(seen[0])
    assert VIDEO_URL not in _all_text(seen[0])


def test_inline_base64_video_is_left_unhandled_by_this_contract():
    """No URL exists for an ``InputContentDataSource`` part, so nothing is added."""
    messages = [
        {
            "id": "m1",
            "role": "user",
            "content": [
                {
                    "type": "video",
                    "source": {
                        "type": "data",
                        "value": "AAAA",
                        "mimeType": "video/mp4",
                    },
                }
            ],
        }
    ]

    annotated = annotate_video_audio_attachments(messages)

    assert annotated == messages


def test_a_message_without_attachments_is_returned_unchanged():
    messages = [{"id": "m1", "role": "user", "content": "hello"}]

    annotated = annotate_video_audio_attachments(messages)

    assert annotated == messages
    assert annotated is not messages  # the input list itself is not mutated in place
