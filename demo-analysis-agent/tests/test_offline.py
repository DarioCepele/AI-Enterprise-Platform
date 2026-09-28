"""Without a model the agent still starts and answers: the offline mode.

The whole platform can run without a credential -- for a first look, for CI.
That only holds if every agent can, not just the master.
"""

from __future__ import annotations

from analysis.agent import build_analysis_agent
from analysis.config import Settings


async def test_offline_the_agent_answers_without_a_credential(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = build_analysis_agent(settings=Settings(fake_client=True))

    response = await agent.run("a question")

    assert response.text == "The analysis agent is running without a model."
