"""Chat clients that answer without a model, for tests and offline runs.

The streaming one lives in platform-core, shared with the subagents; the one
that calls a tool first is this agent's own test helper.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)
from platform_core.fake_model import DEFAULT_CHUNKS, FakeStreamingChatClient

__all__ = ["DEFAULT_CHUNKS", "FakeStreamingChatClient", "ToolCallingFakeClient"]


class ToolCallingFakeClient(
    FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient
):
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
        final_text: str = "Done.",
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
