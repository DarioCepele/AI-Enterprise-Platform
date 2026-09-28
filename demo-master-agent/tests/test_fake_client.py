import pytest
from agent_framework import ChatResponse

from master_agent.chat_clients.fake import FakeStreamingChatClient


@pytest.mark.asyncio
async def test_streaming_yields_one_update_per_chunk():
    client = FakeStreamingChatClient(chunks=["one ", "two ", "three"])

    stream = client._inner_get_response(messages=[], stream=True, options={})
    texts = []
    async for update in stream:
        texts.extend(c.text for c in update.contents if c.text)

    assert texts == ["one ", "two ", "three"]


@pytest.mark.asyncio
async def test_non_streaming_returns_joined_text():
    client = FakeStreamingChatClient(chunks=["one ", "two"])

    response: ChatResponse = await client._inner_get_response(
        messages=[], stream=False, options={}
    )

    assert response.messages[0].text == "one two"
