"""The tools with which the master queries its subagents over A2A."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any
from uuid import uuid4

from a2a.types import AgentCard
from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

from ..a2a.client import A2AClient, Progress, fetch_agent_card
from ..a2a.push import token_for, webhook_url
from ..config import SubagentConfig, get_settings
from ..server.run_context import pending_of_run, subagent_run, thread_of_run

logger = logging.getLogger(__name__)

CARD_TTL_SECONDS = 600

DESCRIPTION = """Queries the {name} agent about a topic.

Every call is independent: the subagent does not see the conversation, so the
question has to stand on its own. To compare two topics, call it twice in the
same turn, so the two queries start together instead of one after the other."""

CardLoader = Callable[[SubagentConfig], Awaitable[AgentCard]]
ClientFactory = Callable[[SubagentConfig, AgentCard], Any]


class Subagent:
    """One remote agent: its card, its client, and when to read them again.

    The card is cached with a TTL because an agent that changes url, capability
    or catalogue would otherwise stay as it was until this process restarts.
    """

    def __init__(
        self,
        config: SubagentConfig,
        card_loader: CardLoader,
        client_factory: ClientFactory,
        ttl_seconds: float,
        now: Callable[[], float],
    ) -> None:
        self.config = config
        self.description = DESCRIPTION.format(name=config.name)
        self._load_card = card_loader
        self._make_client = client_factory
        self._ttl = ttl_seconds
        self._now = now
        self._client: Any = None
        self._read_at = 0.0
        self._tool: FunctionTool | None = None

    @property
    def name(self) -> str:
        return self.config.name

    def attach(self, function_tool: FunctionTool) -> None:
        """The tool the model sees, so a description read from the card reaches it."""
        self._tool = function_tool
        function_tool.description = self.description

    async def client(self) -> Any:
        if self._client is not None and self._now() - self._read_at < self._ttl:
            return self._client

        try:
            card = await self._load_card(self.config)
        except Exception:
            if self._client is None:
                raise
            logger.warning(
                "The card of %s could not be read again: keeping the one in hand.",
                self.config.url,
                exc_info=True,
            )
            self._read_at = self._now()
            return self._client

        if not card.capabilities.streaming:
            logger.warning(
                "The card of %s does not declare streaming: answers will arrive whole.",
                self.config.url,
            )
        self._client = self._make_client(self.config, card)
        self._read_at = self._now()
        self._describe(card)
        await self._enrich_from_the_extended_card(card)
        return self._client

    def _describe(self, card: AgentCard) -> None:
        parts = [DESCRIPTION.format(name=self.config.name)]
        if card.description:
            parts.append(card.description)
        self.description = "\n\n".join(parts)

    async def _enrich_from_the_extended_card(self, card: AgentCard) -> None:
        """Asks for the extended view and puts the catalogue in the description.

        The public card says what the agent can do; which documents it has
        indexed it only tells whoever authenticates. For the model that is the
        difference between asking blindly and knowing what there is to ask for.
        """
        if not self.config.token or not card.capabilities.extended_agent_card:
            return
        extended = await self._client.extended_card(self.config.token)
        if extended is None:
            return
        catalogue = next(
            (s.description for s in extended.skills if s.id == "catalogue"), ""
        )
        if not catalogue:
            return
        logger.info("Extended card of %s: %s", self.config.name, catalogue)
        self.description = f"{self.description}\n\n{catalogue}"
        self._publish()

    def _publish(self) -> None:
        if self._tool is not None:
            self._tool.description = self.description


def build_subagent_tools(
    subagents: Sequence[SubagentConfig],
    card_loader: CardLoader | None = None,
    client_factory: ClientFactory | None = None,
    card_ttl_seconds: float = CARD_TTL_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> list[FunctionTool]:
    """One tool per configured subagent, plus the one that resumes any of them.

    No subagents means no tools: an agent that has none should not carry a tool
    that answers "unreachable" to every call.
    """
    if not subagents:
        return []

    load_card = card_loader or (lambda config: fetch_agent_card(config.url))
    make_client = client_factory or (lambda config, card: A2AClient(card))
    remotes = {
        config.name: Subagent(config, load_card, make_client, card_ttl_seconds, now)
        for config in subagents
    }

    tools = [_ask_tool(remote) for remote in remotes.values()]
    tools.append(_answer_tool(remotes))
    return tools


def _ask_tool(remote: Subagent) -> FunctionTool:
    async def ask(
        question: Annotated[str, "The question to pass to the agent, self-contained"],
    ) -> Content:
        start = time.monotonic()
        settings = get_settings()
        thread_id = thread_of_run()
        webhook = (
            (
                webhook_url(settings.public_url, settings.default_scope, thread_id),
                token_for(thread_id),
            )
            if settings.public_url and thread_id
            else None
        )
        late = False
        pieces: list[str] = []
        states: list[str] = []
        artifact_count = 0
        briefings: list = []
        task_id = ""
        context_id = ""
        question_from_the_subagent = ""

        try:
            client = await remote.client()
            async with subagent_run(remote.name, question):
                progress: Progress | None = None
                try:
                    async with asyncio.timeout(settings.subagent_wait_seconds):
                        async for progress in client.ask(question, webhook=webhook):
                            task_id = progress.task_id or task_id
                            context_id = progress.context_id or context_id
                            if not states or states[-1] != progress.state:
                                states.append(progress.state)
                            if progress.text:
                                pieces.append(progress.text)
                            if progress.artifact:
                                artifact_count += 1
                                briefings.append(progress.artifact)
                                if progress.artifact.text:
                                    pieces.append(progress.artifact.text)
                            if progress.waiting_for_an_answer:
                                question_from_the_subagent = progress.question
                except TimeoutError:
                    late = True
        except Exception:
            logger.error(
                "Agent %s unreachable for '%s'.", remote.name, question, exc_info=True
            )
            return Content.from_text(
                f"The {remote.name} agent did not answer: go on with what you know, "
                "stating that this part is not verified."
            )

        answer = "".join(pieces).strip()
        briefing = next(
            (
                a.data
                for a in briefings
                if a.data and a.data.get("component") == "briefing"
            ),
            None,
        )
        logger.info(
            "Agent %s on '%s': task %s, states %s, %d artifacts in %.2fs, "
            "%d characters.",
            remote.name,
            question,
            task_id[:8] or "?",
            " -> ".join(states) or "none",
            artifact_count,
            time.monotonic() - start,
            len(answer),
        )

        if late:
            logger.info(
                "Task %s of %s outlasts the wait: going on, the outcome will "
                "arrive by webhook.",
                task_id[:8] or "?",
                remote.name,
            )
            partial = f" So far it said: {answer}" if answer else ""
            return Content.from_text(
                f"The {remote.name} agent is still working and I did not wait "
                "any longer."
                + partial
                + " The outcome will arrive as a notification and will be "
                "available next turn: tell the user that instead of inventing "
                "the answer."
            )

        if question_from_the_subagent:
            logger.info(
                "Task %s of %s is waiting for a clarification: %s",
                task_id[:8] or "?",
                remote.name,
                question_from_the_subagent,
            )
            return state_update(
                text=(
                    f"The {remote.name} agent stopped and asks: "
                    f"{question_from_the_subagent}\n"
                    "Pass the question on to the user instead of answering "
                    "in their place. "
                    "When the user answers, use 'answer_subagent'."
                ),
                state={
                    "subagent_pending": {
                        "task_id": task_id,
                        # The conversation the task belongs to travels with it:
                        # answering into a new one is what the agent refuses.
                        "context_id": context_id,
                        "agent": remote.name,
                        "question": question_from_the_subagent,
                        "request": question,
                    }
                },
            )
        if not answer:
            return Content.from_text(
                f"The {remote.name} agent produced no answer about '{question}'."
            )
        if briefing is None:
            return Content.from_text(answer)

        artifact_id = f"{remote.name[:2]}_{task_id[:8] or uuid4().hex[:8]}"
        return state_update(
            text=answer,
            tool_result={
                "component": "briefing",
                "id": artifact_id,
                "agent": remote.name,
                "question": str(briefing.get("question", question)),
                "documents": [str(d) for d in briefing.get("documents", [])],
                "summary": str(briefing.get("summary", answer)),
            },
            state={
                "artifacts": [
                    {
                        "id": artifact_id,
                        "component": "briefing",
                        "title": f"{remote.name}: {question[:60]}",
                    }
                ]
            },
        )

    function_tool = tool(ask, name=f"ask_{remote.name}", description=remote.description)
    remote.attach(function_tool)
    return function_tool


def _answer_tool(remotes: dict[str, Subagent]) -> FunctionTool:
    async def answer_subagent(
        answer: Annotated[
            str, "The user's answer to the clarification the subagent asked for"
        ],
    ) -> Content:
        """Resumes the subagent that asked for a clarification.

        Use it when the user answers a question a subagent passed up to you: the
        conversation resumes from the same task, not from scratch.
        """
        waiting = pending_of_run()
        if not waiting or not waiting.get("task_id"):
            return Content.from_text(
                "No subagent is waiting for an answer: query it from scratch if needed."
            )

        name = str(waiting.get("agent", ""))
        remote = remotes.get(name)
        if remote is None:
            logger.warning(
                "The subagent '%s' that asked is no longer configured.", name
            )
            return Content.from_text(
                f"The agent '{name}' that asked the question is no longer configured: "
                "tell the user, and go on without it."
            )

        task_id = str(waiting["task_id"])
        context_id = str(waiting.get("context_id") or "")
        pieces: list[str] = []
        try:
            client = await remote.client()
            async with subagent_run(remote.name, answer):
                async for progress in client.ask(
                    answer, task_id=task_id, context_id=context_id
                ):
                    if progress.text:
                        pieces.append(progress.text)
                    if progress.artifact and progress.artifact.text:
                        pieces.append(progress.artifact.text)
        except Exception:
            logger.error(
                "Resuming task %s of %s failed.", task_id[:8], name, exc_info=True
            )
            return Content.from_text(
                f"I could not resume the {name} agent: tell the user."
            )

        text = "".join(pieces).strip()
        logger.info(
            "Task %s of %s resumed: %d characters.", task_id[:8], name, len(text)
        )
        return state_update(
            text=text or "The subagent added nothing after the clarification.",
            state={"subagent_pending": {}},
        )

    return tool(answer_subagent)
