"""Context measurement: how much enters the window, on every model call.

The context is a finite resource, and the thresholds for pruning it -- when to
summarize, when to empty tool results -- have to be chosen on measured
numbers, not by eye. This middleware produces those numbers.

It records **counts** only, never message content: the logs end up in the
frontend's LOG tab, and a conversation is not diagnostic material.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_framework import ChatContext, ChatResponse, Message, chat_middleware

logger = logging.getLogger(__name__)

_RESULT_ATTRS = ("result", "output")

def _content_size(content: Any) -> int:
    """Characters of a content, whatever shape it has."""
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return len(text)
    arguments = getattr(content, "arguments", None)
    if arguments is not None:
        return len(str(arguments))
    for attr in _RESULT_ATTRS:
        value = getattr(content, attr, None)
        if value is not None:
            return len(str(value))
    return 0

def _is_tool_result(content: Any) -> bool:
    return any(getattr(content, attr, None) is not None for attr in _RESULT_ATTRS)

def measure(messages: Sequence[Message]) -> dict[str, int]:
    """Size of the outgoing context: messages, characters, the tools' share.

    Characters are a proxy for tokens, available even when the provider does
    not report usage. The ratio is coarse but stable, and it is enough to see
    a curve that grows.
    """
    total = 0
    tool_chars = 0
    for message in messages:
        for content in getattr(message, "contents", None) or []:
            size = _content_size(content)
            total += size
            if _is_tool_result(content):
                tool_chars += size
    return {"messages": len(messages), "chars": total, "tool_chars": tool_chars}

def _usage(response: object) -> dict[str, int]:
    """Tokens actually consumed, when the provider declares them."""
    details = getattr(response, "usage_details", None) or {}
    return {
        "input_tokens": int(details.get("input_token_count") or 0),
        "output_tokens": int(details.get("output_token_count") or 0),
    }

def _log(size: dict[str, int], usage: dict[str, int]) -> None:
    tokens = (
        f"{usage['input_tokens']} tokens in, {usage['output_tokens']} out"
        if usage["input_tokens"] or usage["output_tokens"]
        else "tokens not reported by the provider"
    )
    logger.info(
        "Context: %d messages, %d characters (%d from tools); %s.",
        size["messages"],
        size["chars"],
        size["tool_chars"],
        tokens,
    )

@chat_middleware
async def log_context_size(
    context: ChatContext,
    call_next: Callable[[], Awaitable[None]],
) -> None:
    """Records the size of the context on every model call.

    A run with tools makes more than one call: each has its own line, and that
    is exactly where the context is seen swelling inside a single run.
    """
    size = measure(context.messages)

    def _on_final(response: ChatResponse) -> ChatResponse:
        _log(size, _usage(response))
        return response

    context.stream_result_hooks.append(_on_final)
    await call_next()

    if not context.stream:
        _log(size, _usage(context.result))
