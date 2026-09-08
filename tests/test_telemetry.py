"""La misura del contesto: conteggi si, contenuto no."""
import logging
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest
from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
    UsageDetails,
)

from agent_framework._middleware import ChatMiddlewareLayer

from demo.agents.master import build_master_agent
from demo.telemetry import measure

class UsageReportingClient(ChatMiddlewareLayer, BaseChatClient):
    """Risponde un testo fisso e dichiara un uso di token noto."""

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
            messages=[Message(role="assistant", contents=[Content.from_text("fatto")])],
            usage_details=self._usage,
        )
        if not stream:

            async def _once() -> ChatResponse:
                return reply

            return _once()

        async def _stream():

            yield ChatResponseUpdate(
                contents=[Content.from_text("fatto"), Content.from_usage(usage_details=self._usage)],
                role="assistant",
            )

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)

class SilentUsageClient(UsageReportingClient):
    """Provider che non dichiara alcun uso: capita, e non deve rompere nulla."""

    def __init__(self) -> None:
        super().__init__(input_tokens=0, output_tokens=0)

def test_measure_counts_messages_and_characters():
    messages = [
        Message(role="user", contents=[Content.from_text("dodici caratt")]),
        Message(role="assistant", contents=[Content.from_text("ok")]),
    ]

    size = measure(messages)

    assert size["messages"] == 2
    assert size["chars"] == len("dodici caratt") + len("ok")

def test_measure_separates_the_share_coming_from_tools():
    messages = [
        Message(role="user", contents=[Content.from_text("ciao")]),
        Message(
            role="tool",
            contents=[Content.from_function_result(call_id="c1", result="riga di tabella")],
        ),
    ]

    size = measure(messages)

    assert size["tool_chars"] == len("riga di tabella")
    assert size["chars"] > size["tool_chars"]

def test_measure_survives_an_empty_context():
    assert measure([]) == {"messages": 0, "chars": 0, "tool_chars": 0}

@pytest.mark.asyncio
async def test_a_run_logs_size_and_tokens(caplog):
    agent = build_master_agent(chat_client=UsageReportingClient())

    with caplog.at_level(logging.INFO, logger="demo.telemetry"):
        await agent.run("ciao")

    lines = [r.getMessage() for r in caplog.records if r.name == "demo.telemetry"]
    assert lines, "nessuna riga di telemetria"
    assert "120 token in, 7 out" in lines[-1]
    assert "messaggi" in lines[-1]

@pytest.mark.asyncio
async def test_the_line_never_carries_the_conversation(caplog):
    agent = build_master_agent(chat_client=UsageReportingClient())

    with caplog.at_level(logging.INFO, logger="demo.telemetry"):
        await agent.run("parola-segreta-da-non-loggare")

    lines = [r.getMessage() for r in caplog.records if r.name == "demo.telemetry"]
    assert lines
    assert all("parola-segreta-da-non-loggare" not in line for line in lines)

@pytest.mark.asyncio
async def test_a_provider_without_usage_still_logs_the_size(caplog):
    agent = build_master_agent(chat_client=SilentUsageClient())

    with caplog.at_level(logging.INFO, logger="demo.telemetry"):
        await agent.run("ciao")

    lines = [r.getMessage() for r in caplog.records if r.name == "demo.telemetry"]
    assert lines
    assert "token non riportati dal provider" in lines[-1]

@pytest.mark.asyncio
async def test_streaming_reports_the_tokens_at_the_end(caplog):
    agent = build_master_agent(chat_client=UsageReportingClient())

    with caplog.at_level(logging.INFO, logger="demo.telemetry"):
        async for _ in agent.run("ciao", stream=True):
            pass

    lines = [r.getMessage() for r in caplog.records if r.name == "demo.telemetry"]
    assert lines, "lo streaming non ha prodotto telemetria"
    assert "120 token in, 7 out" in lines[-1]
