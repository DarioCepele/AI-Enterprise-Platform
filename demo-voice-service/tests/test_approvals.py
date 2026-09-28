"""A spoken request that needs a person's approval is cancelled, and said so.

The master agent ends such a run in an AG-UI interrupt. Voice has nowhere to
show the question, and the protocol refuses any further input on a thread
with a question open -- so an unanswered interrupt would leave the voice
session unable to take another turn. These tests pin what the bridge does
instead: cancel the question (the tool does not run), tell the user, keep
going -- also when barge-in cut the turn before it could see the question.

The master agent here is scripted, and keeps the one piece of thread state
that matters the way the real one does (verified against it in
`demo-master-agent/tests/test_approvals.py`): an open question refuses new
messages, a `cancelled` resume closes it, and a run with no input returns
the thread as it stands, ending on its open questions.
"""

from __future__ import annotations

import json

import httpx
import pytest

from voice_service.agui_client import (
    DEFAULT_APPROVAL_NOTICE,
    AGUIBridgeClient,
    AGUIRunError,
)


def sse(*events: dict) -> bytes:
    return b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)


STARTED = {"type": "RUN_STARTED", "threadId": "t", "runId": "r"}
FINISHED = {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"}


def said(text: str) -> list[dict]:
    return [
        STARTED,
        {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": text},
        {"type": "TEXT_MESSAGE_END", "messageId": "m"},
    ]


def interrupted(*ids: str) -> dict:
    return FINISHED | {
        "outcome": {
            "type": "interrupt",
            "interrupts": [{"id": i, "reason": "tool_call"} for i in ids],
        }
    }


def refused(code: str) -> bytes:
    return sse(STARTED, {"type": "RUN_ERROR", "message": code, "code": code})


class ScriptedMaster:
    """Answers each turn with the next scripted reply, like MAF would."""

    def __init__(self, *replies: list[dict], fail_next_cancel: bool = False) -> None:
        self.replies = list(replies)
        self.open: list[str] = []
        self.bodies: list[dict] = []
        self.fail_next_cancel = fail_next_cancel

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if "resume" in body:
            return self._resume(request, body["resume"])
        if not body["messages"]:
            outcome = interrupted(*self.open) if self.open else FINISHED
            return self._stream(sse(STARTED, outcome))
        if self.open:
            return self._stream(refused("APPROVAL_RESUME_REQUIRED"))
        reply = self.replies.pop(0)
        self.open = [
            item["id"]
            for event in reply
            for item in event.get("outcome", {}).get("interrupts", [])
        ]
        return self._stream(sse(*reply))

    def _resume(self, request: httpx.Request, resume: list[dict]) -> httpx.Response:
        if self.fail_next_cancel:
            self.fail_next_cancel = False
            raise httpx.ConnectError("master unreachable", request=request)
        if sorted(r["interruptId"] for r in resume) != sorted(self.open):
            return self._stream(refused("APPROVAL_RESUME_NOT_FOUND"))
        assert all(r["status"] == "cancelled" for r in resume)
        self.open = []
        return self._stream(sse(STARTED, FINISHED))

    @staticmethod
    def _stream(content: bytes) -> httpx.Response:
        return httpx.Response(
            200, content=content, headers={"content-type": "text/event-stream"}
        )

    @property
    def cancels(self) -> list[list[dict]]:
        return [b["resume"] for b in self.bodies if "resume" in b]


def bridge_for(master: ScriptedMaster, **kwargs) -> AGUIBridgeClient:
    return AGUIBridgeClient(
        "http://master", transport=httpx.MockTransport(master), **kwargs
    )


async def test_an_interrupted_turn_is_cancelled_and_says_why():
    master = ScriptedMaster([*said("Starting it."), interrupted("i-1")])
    bridge = bridge_for(master)

    chunks = [chunk async for chunk in bridge.stream_turn("start the payment")]

    assert chunks == ["Starting it.", " " + DEFAULT_APPROVAL_NOTICE]
    assert master.cancels == [[{"interruptId": "i-1", "status": "cancelled"}]]
    [cancel] = [b for b in master.bodies if "resume" in b]
    assert cancel["messages"] == []
    assert cancel["threadId"] == master.bodies[0]["threadId"]
    assert master.open == []


async def test_the_notice_stands_alone_when_nothing_was_said():
    master = ScriptedMaster([STARTED, interrupted("i-1")])
    bridge = bridge_for(master, approval_notice="  Serve un'approvazione.  ")

    chunks = [chunk async for chunk in bridge.stream_turn("avvia il pagamento")]

    assert chunks == ["Serve un'approvazione."]


async def test_every_open_question_is_cancelled_at_once():
    """AG-UI: one resume addresses every open interrupt; partial is refused."""
    master = ScriptedMaster([STARTED, interrupted("i-1", "i-2")])
    bridge = bridge_for(master)

    [_ async for _ in bridge.stream_turn("do both")]

    assert master.cancels == [
        [
            {"interruptId": "i-1", "status": "cancelled"},
            {"interruptId": "i-2", "status": "cancelled"},
        ]
    ]


async def test_the_next_turn_goes_through_after_a_cancelled_question():
    master = ScriptedMaster(
        [*said("Starting it."), interrupted("i-1")],
        [*said("Fine."), FINISHED],
    )
    bridge = bridge_for(master)

    [_ async for _ in bridge.stream_turn("start the payment")]
    second = [chunk async for chunk in bridge.stream_turn("never mind")]

    assert second == ["Fine."]
    assert len(master.cancels) == 1


async def test_a_turn_cut_short_is_cleared_up_by_the_next_one():
    """Barge-in stops reading before the run's end, so the bridge never sees
    the question: the next turn is refused, reads it, cancels it, retries."""
    master = ScriptedMaster(
        [*said("Starting it. "), interrupted("i-1")],
        [*said("Fine."), FINISHED],
    )
    bridge = bridge_for(master)

    reply = bridge.stream_turn("start the payment")
    await anext(reply)
    await reply.aclose()
    assert master.open == ["i-1"] and master.cancels == []

    second = [chunk async for chunk in bridge.stream_turn("never mind")]

    assert second == ["Fine."]
    assert master.cancels == [[{"interruptId": "i-1", "status": "cancelled"}]]
    assert master.open == []


async def test_a_failed_cancel_is_retried_before_the_next_turn():
    master = ScriptedMaster(
        [*said("Starting it."), interrupted("i-1")],
        [*said("Fine."), FINISHED],
        fail_next_cancel=True,
    )
    bridge = bridge_for(master)

    with pytest.raises(httpx.ConnectError):
        [_ async for _ in bridge.stream_turn("start the payment")]
    second = [chunk async for chunk in bridge.stream_turn("never mind")]

    assert second == ["Fine."]
    assert master.open == []


async def test_other_run_errors_still_surface_with_their_code():
    error = {"type": "RUN_ERROR", "message": "boom", "code": "X"}
    master = ScriptedMaster([STARTED, error])
    bridge = bridge_for(master)

    with pytest.raises(AGUIRunError) as caught:
        [_ async for _ in bridge.stream_turn("hi")]

    assert caught.value.code == "X"
    assert str(caught.value) == "boom"


async def test_a_plain_turn_sends_no_cancel():
    master = ScriptedMaster([*said("Hello."), FINISHED])
    bridge = bridge_for(master)

    assert [chunk async for chunk in bridge.stream_turn("hi")] == ["Hello."]
    assert master.cancels == []
    assert len(master.bodies) == 1
