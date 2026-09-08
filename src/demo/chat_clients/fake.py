"""Fake chat client: emits deterministic chunks, without network.

It serves the tests and offline development. It isolates the rest of the system
from the LLM.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)
from agent_framework._middleware import ChatMiddlewareLayer
from agent_framework._tools import FunctionInvocationLayer

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

        async def _stream():
            for chunk in self._chunks:
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield ChatResponseUpdate(
                    contents=[Content.from_text(chunk)], role="assistant"
                )

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)


class ToolCallingFakeClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """First round: calls `tool_name` with `tool_args`. Later rounds: text.

    It inherits from FunctionInvocationLayer, without which Agent does not run
    tools, and from ChatMiddlewareLayer: the same layer order as the real
    OpenAI client. It exists to test the TOOL_CALL_* -> STATE_SNAPSHOT chain
    offline.
    """

    def __init__(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        final_text: str = "Fatto.",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_text = final_text
        self._turn = 0

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self._turn += 1
        if self._turn == 1:
            contents = [
                Content.from_function_call(
                    call_id="call_1", name=self._tool_name, arguments=self._tool_args
                )
            ]
        else:
            contents = [Content.from_text(self._final_text)]

        if not stream:

            async def _once() -> ChatResponse:
                return ChatResponse(
                    messages=[Message(role="assistant", contents=contents)]
                )

            return _once()

        async def _stream():
            for content in contents:
                yield ChatResponseUpdate(contents=[content], role="assistant")

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)
