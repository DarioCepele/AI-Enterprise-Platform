"""A step that is given a goal instead of a path.

Every other step in this engine says what to do. This one says what to reach,
and lets a manager decide who works on it and in which order -- the Magentic
orchestration of `agent-framework-orchestrations`, with the laboratory's A2A
agents as participants.

It exists because some work genuinely has no fixed path. It is also the most
expensive thing in the engine and the easiest to leave running, so the ceilings
are not optional: the definition declares rounds, agents and tokens, and the
node stops at whichever comes first. What comes out is a plain step output --
text plus what it cost -- so that the history, and the replay, do not have to
know anything about how it was reached.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_framework import AgentResponse

logger = logging.getLogger(__name__)

STOPPED_BY_BUDGET = "token budget"
STOPPED_BY_ROUNDS = "round limit"
FINISHED = "the manager finished"


@dataclass
class Spent:
    """What the node has used so far, shared by everyone taking part."""

    max_tokens: int
    rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_agent: dict[str, int] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def exhausted(self) -> bool:
        return self.tokens >= self.max_tokens

    def add(self, agent: str, usage: dict[str, int]) -> None:
        self.rounds += 1
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)
        self.by_agent[agent] = self.by_agent.get(agent, 0) + 1

    def as_data(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_agent": dict(self.by_agent),
        }


class RemoteParticipant:
    """One A2A agent, in the shape the orchestrator expects.

    The orchestrator wants something it can call and get an answer from, in one
    go: inside this node the long-wait machinery of an `agent` step does not
    apply, because the manager is holding the conversation. That is part of why
    the node is capped -- what happens in here is not durable the way a step is.
    """

    def __init__(self, name: str, description: str, gateway: Any, spent: Spent) -> None:
        self.id = f"{name}-{uuid4().hex[:8]}"
        self.name = name
        self.description = description
        self._gateway = gateway
        self._spent = spent

    async def run(self, messages: Any = None, *, stream: bool = False, **kwargs: Any) -> Any:
        question = _as_text(messages)
        if self._spent.exhausted:
            # Refusing here rather than raising: the manager reads it as an
            # answer, writes it into the ledger, and wraps up on its own.
            return _answer(f"Stop: {STOPPED_BY_BUDGET} spent for this step.")

        result = await self._gateway.converse(agent=self.name, question=question)
        self._spent.add(self.name, result.get("usage") or {})
        logger.info(
            "Open goal: %s answered (%d characters), %d tokens spent of %d.",
            self.name,
            len(result.get("text", "")),
            self._spent.tokens,
            self._spent.max_tokens,
        )
        return _answer(result.get("text", ""))

    def create_session(self, *, session_id: str | None = None) -> Any:
        from agent_framework import AgentSession

        return AgentSession(session_id=session_id)

    def get_session(self, service_session_id: Any, *, session_id: str | None = None) -> Any:
        from agent_framework import AgentSession

        return AgentSession(service_session_id=service_session_id, session_id=session_id)


def _answer(text: str) -> Any:
    from agent_framework import Message

    return AgentResponse(messages=[Message("assistant", [text])], response_id=uuid4().hex)


def _last_output(result: Any) -> str:
    """The answer the orchestration ended on.

    A workflow yields outputs as it goes; the one that matters for a step is the
    last, which is what the manager wrote when it decided the goal was reached.
    """
    outputs = [output for output in result.get_outputs() if output is not None]
    for output in reversed(outputs):
        text = output if isinstance(output, str) else getattr(output, "text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _as_text(messages: Any) -> str:
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages
    if isinstance(messages, (list, tuple)):
        return "\n".join(_as_text(message) for message in messages)
    text = getattr(messages, "text", None)
    return text if isinstance(text, str) else str(messages)


def manager_agent() -> Any:
    """The model that plans, watches progress and decides when it is done.

    It is a model call per round on top of the agents' own: an open goal costs
    more than the same work written down as steps, and that is the trade being
    made, not an implementation detail.
    """
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    return Agent(
        name="manager",
        description="Plans the work of an open goal and decides when it is finished.",
        client=OpenAIChatCompletionClient(
            model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
        ),
    )


async def pursue(
    *,
    goal: str,
    participants: list[tuple[str, str]],
    gateway: Any,
    max_rounds: int,
    max_tokens: int,
    manager: Any = None,
) -> dict[str, Any]:
    """Runs the open goal and returns what it reached, and what it cost.

    The ceilings are enforced in two places on purpose: the round limit belongs
    to the orchestrator, which knows how to stop cleanly, and the token budget
    belongs to the participants, because they are the ones spending it.
    """
    from agent_framework_orchestrations import MagenticBuilder

    spent = Spent(max_tokens=max_tokens)
    people = [
        RemoteParticipant(name, description, gateway, spent)
        for name, description in participants
    ]

    workflow = MagenticBuilder(
        participants=people,
        manager_agent=manager or manager_agent(),
        max_round_count=max_rounds,
    ).build()

    result = await workflow.run(goal)
    text = _last_output(result)

    stopped_by = FINISHED
    if spent.exhausted:
        stopped_by = STOPPED_BY_BUDGET
    elif spent.rounds >= max_rounds:
        stopped_by = STOPPED_BY_ROUNDS

    logger.info(
        "Open goal finished (%s): %d rounds, %d tokens, agents %s.",
        stopped_by,
        spent.rounds,
        spent.tokens,
        ", ".join(spent.by_agent) or "none",
    )
    return {"text": text, "stopped_by": stopped_by, **spent.as_data()}
