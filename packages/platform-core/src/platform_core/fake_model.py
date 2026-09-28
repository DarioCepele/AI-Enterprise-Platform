"""A chat client that answers without a model: deterministic chunks, no network.

For tests, and for running the platform offline -- every agent of the
platform can be switched to it (`*_FAKE_CLIENT`), so the whole stack starts
and answers without a credential.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

DEFAULT_CHUNKS = ["I am ", "working ", "on the ", "answer."]


class FakeStreamingChatClient(ChatMiddlewareLayer, BaseChatClient):
    """Emits `chunks` one at a time, `delay` seconds apart.

    It inherits from ChatMiddlewareLayer like the real OpenAI client: without
    that layer the middleware mounted on the agent would not run in the tests,
    and telemetry would look green here and be absent in production.
    """

    def __init__(self, chunks: list[str] | None = None, delay: float = 0.0) -> None:
        super().__init__()
        self._chunks = chunks if chunks is not None else list(DEFAULT_CHUNKS)
        self._delay = delay

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        if not stream:

            async def _once() -> ChatResponse:
                return ChatResponse(
                    messages=[
                        Message(
                            role="assistant",
                            contents=[Content.from_text("".join(self._chunks))],
                        )
                    ]
                )

            return _once()

        async def _stream() -> AsyncIterator[ChatResponseUpdate]:
            for chunk in self._chunks:
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield ChatResponseUpdate(
                    contents=[Content.from_text(chunk)], role="assistant"
                )

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)
