"""The transcript-to-AG-UI bridge, against a real `demo-master-agent` run.

`demo-master-agent` is started as a subprocess, from its own repo and its
own virtualenv (a sibling of this one), with `DEMO_FAKE_CLIENT=true` -- the
same offline/deterministic client its own tests use
(`demo-master-agent/tests/test_fake_client.py`,
`demo-master-agent/src/demo/chat_clients/fake.py`): no network call, no API
key, no flakiness. With no chat client passed in, `build_master_agent()`
picks `FakeStreamingChatClient()` with its default chunks
(`["I am ", "working ", "on the ", "answer."]`), so any turn answers
"I am working on the answer." -- fixed and known ahead of time.

`DEMO_POSTGRES_DSN` and `DEMO_MEMORY_SERVICE_URL` are forced empty for the
subprocess regardless of `demo-master-agent/.env`: this test needs neither a
shared-state Postgres nor a memory service, and `python-dotenv`'s
`load_dotenv()` (called at import in `demo/config.py`) does not override a
variable already present in the environment -- so setting it here, even to
"", wins over the `.env` file.

This is a real subprocess (uvicorn serving `demo.server.app:create_app`),
not the in-process ASGI-transport pattern `demo-master-agent`'s own tests
use for its `app` fixture: that pattern needs `demo-master-agent`'s package
and its dependencies (`agent-framework`, `a2a-sdk`, `psycopg`,
`opentelemetry`, ...) importable from *this* project's interpreter, which
would mean adding `demo-master-agent` as a dependency of
`demo-voice-service` -- out of scope for this contract's `pyproject.toml`
allowance (only a missing HTTP client). A subprocess, run with
`demo-master-agent`'s own already-synced virtualenv, needs none of that.

The fixture that starts it (`master_agent_url`) lives in `conftest.py`, not
here: `test_barge_in.py` and `test_end_to_end.py` need it too, and a
fixture shared across modules belongs in `conftest.py`, pytest's own
mechanism for exactly that.
"""
from __future__ import annotations

from voice_service.agui_client import AGUIBridgeClient

FIXED_TRANSCRIPT = "what is the status of my plan today"
# FakeStreamingChatClient()'s own default chunks, joined -- see
# demo-master-agent/src/demo/chat_clients/fake.py: DEFAULT_CHUNKS.
EXPECTED_REPLY = "I am working on the answer."


async def test_a_fixed_transcript_gets_a_nonempty_predictable_reply(master_agent_url):
    """The bridge, against a real run: a fixed turn, an assistant_text-ready reply.

    Also checks thread continuity: a second turn on the same bridge (one AG-UI
    thread per voice session, see `AGUIBridgeClient`) still gets answered --
    one subprocess covers both checks, keeping the suite's runtime down.
    """
    bridge = AGUIBridgeClient(master_agent_url)

    reply = await bridge.send_turn(FIXED_TRANSCRIPT)
    assert reply == EXPECTED_REPLY

    second_reply = await bridge.send_turn("a second, unrelated turn")
    assert second_reply == EXPECTED_REPLY


async def test_stream_turn_yields_chunks_that_join_back_to_the_same_reply(
    master_agent_url,
):
    """`stream_turn` (added for Step 4 of the plan's Tappa 1 -- feeding TTS as
    text arrives) against the same real run: it must yield at least one
    chunk, and joining every chunk it yields must reproduce exactly what
    `send_turn` returns for the same input -- the property `send_turn`'s own
    docstring relies on, now checked directly against a real AG-UI stream
    instead of only reasoned about.
    """
    bridge = AGUIBridgeClient(master_agent_url)

    chunks = [chunk async for chunk in bridge.stream_turn(FIXED_TRANSCRIPT)]
    assert len(chunks) >= 1
    assert "".join(chunks) == EXPECTED_REPLY
