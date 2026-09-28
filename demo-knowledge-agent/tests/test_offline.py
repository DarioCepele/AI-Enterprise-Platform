"""Without a model the agent still starts and answers: the offline mode.

The whole platform can run without a credential -- for a first look, for CI.
That only holds if every agent can, not just the master.
"""

from __future__ import annotations

from knowledge.agent import build_knowledge_agent
from knowledge.config import Settings


async def test_offline_the_agent_answers_without_a_credential(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = build_knowledge_agent(settings=Settings(fake_client=True))

    response = await agent.run("a question")

    assert response.text == "The knowledge agent is running without a model."
