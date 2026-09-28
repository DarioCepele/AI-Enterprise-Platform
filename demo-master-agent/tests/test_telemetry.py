"""Context measurement: counts yes, content no."""

import logging
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest
from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
    UsageDetails,
)

from master_agent.agents.master import build_master_agent
from master_agent.telemetry import measure


class UsageReportingClient(ChatMiddlewareLayer, BaseChatClient):
    """Answers a fixed text and declares a known token usage."""

    def __init__(self, input_tokens: int = 120, output_tokens: int = 7) -> None:
        super().__init__()
        self._usage = UsageDetails(
            input_token_count=input_tokens, output_token_count=output_tokens
        )

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        reply = ChatResponse(
            messages=[Message(role="assistant", contents=[Content.from_text("done")])],
            usage_details=self._usage,
        )
        if not stream:

            async def _once() -> ChatResponse:
                return reply

            return _once()

        async def _stream():

            yield ChatResponseUpdate(
                contents=[
                    Content.from_text("done"),
                    Content.from_usage(usage_details=self._usage),
                ],
                role="assistant",
            )

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)


class SilentUsageClient(UsageReportingClient):
    """A provider declaring no usage: it happens, and must break nothing."""

    def __init__(self) -> None:
        super().__init__(input_tokens=0, output_tokens=0)


def test_measure_counts_messages_and_characters():
    messages = [
        Message(role="user", contents=[Content.from_text("thirteen char")]),
        Message(role="assistant", contents=[Content.from_text("ok")]),
    ]

    size = measure(messages)

    assert size["messages"] == 2
    assert size["chars"] == len("thirteen char") + len("ok")


def test_measure_separates_the_share_coming_from_tools():
    messages = [
        Message(role="user", contents=[Content.from_text("hello")]),
        Message(
            role="tool",
            contents=[Content.from_function_result(call_id="c1", result="a table row")],
        ),
    ]

    size = measure(messages)

    assert size["tool_chars"] == len("a table row")
    assert size["chars"] > size["tool_chars"]


def test_measure_survives_an_empty_context():
    assert measure([]) == {"messages": 0, "chars": 0, "tool_chars": 0}


@pytest.mark.asyncio
async def test_a_run_logs_size_and_tokens(caplog):
    agent = build_master_agent(chat_client=UsageReportingClient())

    with caplog.at_level(logging.INFO, logger="master_agent.telemetry"):
        await agent.run("hello")

    lines = [
        r.getMessage() for r in caplog.records if r.name == "master_agent.telemetry"
    ]
    assert lines, "no telemetry line"
    assert "120 tokens in, 7 out" in lines[-1]
    assert "messages" in lines[-1]


@pytest.mark.asyncio
async def test_the_line_never_carries_the_conversation(caplog):
    agent = build_master_agent(chat_client=UsageReportingClient())

    with caplog.at_level(logging.INFO, logger="master_agent.telemetry"):
        await agent.run("secret-word-not-to-log")

    lines = [
        r.getMessage() for r in caplog.records if r.name == "master_agent.telemetry"
    ]
    assert lines
    assert all("secret-word-not-to-log" not in line for line in lines)


@pytest.mark.asyncio
async def test_a_provider_without_usage_still_logs_the_size(caplog):
    agent = build_master_agent(chat_client=SilentUsageClient())

    with caplog.at_level(logging.INFO, logger="master_agent.telemetry"):
        await agent.run("hello")

    lines = [
        r.getMessage() for r in caplog.records if r.name == "master_agent.telemetry"
    ]
    assert lines
    assert "tokens not reported by the provider" in lines[-1]


@pytest.mark.asyncio
async def test_streaming_reports_the_tokens_at_the_end(caplog):
    agent = build_master_agent(chat_client=UsageReportingClient())

    with caplog.at_level(logging.INFO, logger="master_agent.telemetry"):
        async for _ in agent.run("hello", stream=True):
            pass

    lines = [
        r.getMessage() for r in caplog.records if r.name == "master_agent.telemetry"
    ]
    assert lines, "streaming produced no telemetry"
    assert "120 tokens in, 7 out" in lines[-1]
