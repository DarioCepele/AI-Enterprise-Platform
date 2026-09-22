"""Bridges one transcribed turn to `demo-master-agent`'s existing AG-UI endpoint.

`demo-master-agent` exposes its agent over AG-UI at `POST /agui`
(`demo-master-agent/src/demo/server/app.py`): a JSON body naming the
thread/run and the new messages, answered with a `text/event-stream` of
AG-UI protocol events -- confirmed against
`demo-master-agent/tests/test_agui_stream.py` and by running the app
itself. This module sends a turn's transcript as a plain user message
(no `AudioInputPart`, per the plan: the audio never crosses the protocol,
it already left the voice service as text).

Two ways to read the reply: `send_turn` drains the run's SSE stream to
its end and hands back the whole reply as one string (used where nothing
needs the text before the run finishes). `stream_turn` is the one Step 4
of the plan needs -- it yields the reply incrementally, sentence by
sentence, without waiting for `RUN_FINISHED`, so `voice_service.tts` can
start synthesizing speech before the model has finished writing. `send_turn`
is now built on top of `stream_turn` (their output is identical -- see
that method's docstring for why concatenating its chunks loses nothing).
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

AGUI_PATH = "/agui"


class AGUIRunError(RuntimeError):
    """The master agent's run ended in a `RUN_ERROR` event."""


class AGUIBridgeClient:
    """One AG-UI thread, reused across the turns of a single voice session.

    A stable `thread_id` for the life of the client mirrors how a real
    conversation works: turn two should still see turn one, the same way it
    would in the text chat this bridges to. `run_id` is fresh per turn --
    each turn is its own AG-UI run.
    """

    def __init__(
        self, base_url: str, *, thread_id: str | None = None, timeout: float = 30.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._thread_id = thread_id or uuid4().hex
        self._timeout = timeout

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
        `RUN_FINISHED` before yielding -- that is the point of this method
        (Step 4 of the plan's Tappa 1: TTS has to start on the first
        sentence while the model is still writing the rest).

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

        Raises `AGUIRunError` if the run reports `RUN_ERROR`.
        """
        request_body = {
            "threadId": self._thread_id,
            "runId": uuid4().hex,
            "state": {},
            "messages": [{"id": uuid4().hex, "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        buffer = ""
        async with (
            httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client,
            client.stream(
                "POST",
                AGUI_PATH,
                json=request_body,
                headers={"Accept": "text/event-stream"},
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                event_type = event.get("type")
                if event_type == "TEXT_MESSAGE_CONTENT":
                    delta = event.get("delta")
                    if not delta:
                        continue
                    buffer += delta
                    buffer, sentences = _pop_complete_sentences(buffer)
                    for sentence in sentences:
                        yield sentence
                elif event_type == "RUN_ERROR":
                    raise AGUIRunError(event.get("message") or "AG-UI run failed")

        if buffer:
            yield buffer


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
