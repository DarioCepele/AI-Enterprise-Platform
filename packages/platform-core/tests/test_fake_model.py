"""The model every agent can run on offline: fixed text, streamed or whole."""

from __future__ import annotations

from agent_framework import Agent

from platform_core.fake_model import DEFAULT_CHUNKS, FakeStreamingChatClient


async def test_a_whole_answer_is_the_chunks_joined():
    agent = Agent(name="offline", client=FakeStreamingChatClient(["one ", "two"]))

    response = await agent.run("anything")

    assert response.text == "one two"


async def test_a_streamed_answer_arrives_chunk_by_chunk():
    agent = Agent(name="offline", client=FakeStreamingChatClient())

    chunks = [update.text async for update in agent.run("anything", stream=True)]

    assert [c for c in chunks if c] == DEFAULT_CHUNKS
