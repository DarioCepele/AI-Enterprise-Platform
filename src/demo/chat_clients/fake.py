"""Chat client finto: emette chunk deterministici, senza rete.

Serve ai test e allo sviluppo offline. Isola il resto del sistema dall'LLM.
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
from agent_framework._tools import FunctionInvocationLayer

DEFAULT_CHUNKS = ["Sto ", "elaborando ", "la ", "risposta."]


class FakeStreamingChatClient(BaseChatClient):
    """Emette `chunks` uno alla volta, con `delay` secondi di distanza."""

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


class ToolCallingFakeClient(FunctionInvocationLayer, BaseChatClient):
    """Primo giro: chiama `tool_name` con `tool_args`. Giri successivi: testo.

    Eredita da FunctionInvocationLayer, senza il quale Agent non esegue i tool.
    Serve a testare offline la catena TOOL_CALL_* -> STATE_SNAPSHOT.
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
