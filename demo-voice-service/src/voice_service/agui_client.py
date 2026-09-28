"""Bridges one transcribed turn to the master agent's AG-UI endpoint.

The master agent answers `POST /agui`: a JSON body naming the thread, the run
and the new messages, answered with a `text/event-stream` of AG-UI events.
This module sends a turn's transcript as a plain user message: the audio never
crosses the protocol, it already left this service as text.

Two ways to read the reply: `send_turn` drains the stream and hands back the
whole reply as one string; `stream_turn` yields it sentence by sentence,
without waiting for `RUN_FINISHED`, so speech synthesis can start on the first
sentence while the model is still writing the rest. `send_turn` is built on
top of `stream_turn`, and concatenating its chunks loses nothing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

AGUI_PATH = "/agui"


DEFAULT_APPROVAL_NOTICE = (
    "That needs a person's approval, which I can't take by voice."
    " Ask for it in the chat to approve it there."
)


class AGUIRunError(RuntimeError):
    """The master agent's run ended in a `RUN_ERROR` event."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# The protocol's answer to a new message on a thread with a question open.
RESUME_REQUIRED = "APPROVAL_RESUME_REQUIRED"


class AGUIBridgeClient:
    """One AG-UI thread, reused across the turns of a single voice session.

    A stable `thread_id` for the life of the client mirrors how a real
    conversation works: turn two should still see turn one, the same way it
    would in the text chat this bridges to. `run_id` is fresh per turn --
    each turn is its own AG-UI run.

    Approvals: a tool that needs a person's yes ends the run in an AG-UI
    interrupt, and a voice turn has nowhere to show the question. The
    protocol does not let the thread move on with a question open -- every
    later input must resolve it -- so the bridge cancels it (nothing runs)
    and says so, instead of leaving the session unable to take another turn.
    A turn cut short by barge-in never reads the end of its run, so it never
    learns which question it left open: the next turn is then refused, and
    reads the open questions from the thread itself before trying again.
    """

    def __init__(
        self,
        base_url: str,
        *,
        thread_id: str | None = None,
        timeout: float = 30.0,
        approval_notice: str = DEFAULT_APPROVAL_NOTICE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._thread_id = thread_id or uuid4().hex
        self._timeout = timeout
        self._approval_notice = approval_notice
        self._transport = transport
        # Questions a finished turn left open and could not cancel yet.
        self._open_interrupts: list[str] = []

    def _body(self, **fields: object) -> dict[str, object]:
        return {
            "threadId": self._thread_id,
            "runId": uuid4().hex,
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            **fields,
        }

    def _turn_body(self, text: str) -> dict[str, object]:
        return self._body(
            messages=[{"id": uuid4().hex, "role": "user", "content": text}]
        )

    async def _events(
        self, body: dict[str, object]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """POSTs one run and yields its events as they arrive.

        Raises `AGUIRunError` on `RUN_ERROR`, with the protocol's error code.
        """
        async with (
            httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client,
            client.stream(
                "POST", AGUI_PATH, json=body, headers={"Accept": "text/event-stream"}
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                if event.get("type") == "RUN_ERROR":
                    raise AGUIRunError(
                        event.get("message") or "AG-UI run failed", event.get("code")
                    )
                yield event

    async def _cancel_open_interrupts(self) -> None:
        """Resolves the open questions as `cancelled`: the tool does not run."""
        if not self._open_interrupts:
            return
        resume = [
            {"interruptId": interrupt_id, "status": "cancelled"}
            for interrupt_id in self._open_interrupts
        ]
        async for _ in self._events(self._body(resume=resume)):
            pass
        logger.info(
            "Voice turn needed an approval: %d question(s) cancelled.", len(resume)
        )
        self._open_interrupts = []

    async def _read_open_interrupts(self) -> None:
        """Asks the thread for its open questions: a run with no input
        returns the thread as it stands, ending on what it is waiting for."""
        async for event in self._events(self._body()):
            if event.get("type") == "RUN_FINISHED":
                self._open_interrupts = _interrupt_ids(event)

    async def _turn_events(self, text: str) -> AsyncGenerator[dict[str, Any], None]:
        """The events of one turn, clearing the way first if the thread has a
        question open that this bridge did not see (see the class)."""
        await self._cancel_open_interrupts()
        underway = False  # something past RUN_STARTED reached the caller
        try:
            async for event in self._events(self._turn_body(text)):
                underway = underway or event.get("type") != "RUN_STARTED"
                yield event
            return
        except AGUIRunError as error:
            if error.code != RESUME_REQUIRED or underway:
                raise
        # Refused before a word was said: there is nothing to take back.
        await self._read_open_interrupts()
        await self._cancel_open_interrupts()
        async for event in self._events(self._turn_body(text)):
            yield event

    async def send_turn(self, text: str) -> str:
        """Sends `text` as a user message and returns the assistant's full reply.

        Drains `stream_turn` to its end and concatenates its chunks. That is
        lossless: `stream_turn` never strips or rewrites the text it yields,
        only splits it at sentence boundaries, so joining its output back
        together with `""` reproduces exactly what `send_turn` returned
        before it was reimplemented on top of `stream_turn` (same
        `TEXT_MESSAGE_CONTENT` deltas, same concatenation, just handed back
        in fewer, larger pieces here). Raises `AGUIRunError` if the run
        reports `RUN_ERROR`.
        """
        reply_parts = [chunk async for chunk in self.stream_turn(text)]
        return "".join(reply_parts)

    async def stream_turn(self, text: str) -> AsyncGenerator[str, None]:
        """Sends `text` as a user message and yields the assistant's reply
        incrementally.

        Consumes the run's SSE stream *as it arrives* and never waits for
        `RUN_FINISHED` before yielding -- that is the point of this method:
        speech has to start on the first sentence while the model is still
        writing the rest.

        Chosen grouping: by sentence, not by raw SSE delta. A single
        `TEXT_MESSAGE_CONTENT` delta can be a few characters or a sub-word
        fragment (`demo-master-agent`'s `FakeStreamingChatClient` -- used by
        `tests/test_agui_bridge.py` -- defaults to
        `["I am ", "working ", "on the ", "answer."]`); handing fragments
        like `"I am "` straight to a TTS engine one at a time would ask it
        to synthesize prosody-less scraps instead of words that make sense
        together. Buffering until a sentence-ending `.`/`!`/`?` (or the end
        of the stream, for whatever trailing text never got one) gives
        `voice_service.tts.synthesize` a complete clause to work with, while
        still starting well before the whole reply is in. This is a plain
        punctuation heuristic, not a real sentence-boundary detector -- it
        will split "Dr. Smith" mid-abbreviation, for instance; good enough
        for a demo pipeline reading assistant prose, not a general-purpose
        text splitter.

        No text is stripped or dropped: each yielded piece keeps whatever
        leading/trailing whitespace it had in the source deltas, so
        `"".join()` over everything this yields reconstructs the reply
        byte-for-byte (see `send_turn`).

        A run that stops for an approval ends with the approval notice as its
        last chunk, after its questions are cancelled (see the class).

        Raises `AGUIRunError` if the run reports `RUN_ERROR`.
        """
        buffer = ""
        spoke = False
        async for event in self._turn_events(text):
            event_type = event.get("type")
            if event_type == "TEXT_MESSAGE_CONTENT":
                delta = event.get("delta")
                if not delta:
                    continue
                buffer += delta
                buffer, sentences = _pop_complete_sentences(buffer)
                for sentence in sentences:
                    spoke = True
                    yield sentence
            elif event_type == "RUN_FINISHED":
                self._open_interrupts = _interrupt_ids(event)

        if buffer:
            spoke = True
            yield buffer
        if self._open_interrupts:
            await self._cancel_open_interrupts()
            yield (" " if spoke else "") + self._approval_notice.strip()


def _interrupt_ids(finished: dict[str, object]) -> list[str]:
    """The ids of the questions a `RUN_FINISHED` left open, if any."""
    outcome = finished.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("type") != "interrupt":
        return []
    interrupts = outcome.get("interrupts")
    if not isinstance(interrupts, list):
        return []
    return [
        item["id"]
        for item in interrupts
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


_SENTENCE_ENDERS = (".", "!", "?")


def _pop_complete_sentences(buffer: str) -> tuple[str, list[str]]:
    """Splits `buffer` at each sentence-ending punctuation mark it contains.

    Returns `(remainder, sentences)`: every complete sentence found, in
    order, and whatever is left after the last one (a partial sentence, or
    `""`). Nothing is stripped -- see `stream_turn`'s docstring.
    """
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(buffer):
        if char in _SENTENCE_ENDERS:
            sentences.append(buffer[start : index + 1])
            start = index + 1
    return buffer[start:], sentences
