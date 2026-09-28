"""Shared fixtures: every test runs offline, whatever sits in a local .env."""

from __future__ import annotations

import pytest
from agent_framework.openai import OpenAIChatCompletionClient

from knowledge.agent import build_knowledge_agent


@pytest.fixture
def offline_agent():
    """The real agent, wired to a client that is never called.

    The card endpoints and the middleware under test never reach the model, so
    a client pointed at an unroutable address with a placeholder key proves
    they do not -- and no test needs a credential to pass.
    """
    client = OpenAIChatCompletionClient(
        model="offline",
        api_key="offline-placeholder",
        base_url="http://offline.invalid/v1",
    )
    return build_knowledge_agent(chat_client=client)
