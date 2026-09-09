"""The tool the master uses to query the knowledge agent over A2A."""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import uuid4

from a2a.types import AgentCard
from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

import asyncio

from ..a2a.client import A2AClient, Progress, fetch_agent_card
from ..a2a.push import token_for, webhook_url
from ..config import get_settings
from ..server.run_context import pending_of_run, subagent_run, thread_of_run

logger = logging.getLogger(__name__)

CARD_TTL_SECONDS = 600

DESCRIPTION = """Queries the knowledge base agent about a topic.

Every call is independent: the subagent does not see the conversation, so the
question has to stand on its own. To compare two topics, call it twice in the
same turn, so the two queries start together instead of one after the other."""


def build_subagent_tools(
    url: str,
    card_loader: Callable[[], Awaitable[AgentCard]] | None = None,
    client_factory: Callable[[AgentCard], Any] | None = None,
    card_ttl_seconds: float = CARD_TTL_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> list[FunctionTool]:
    load_card = card_loader or (lambda: fetch_agent_card(url))
    make_client = client_factory or (lambda card: A2AClient(card))
    cached: dict[str, Any] = {}

    async def client() -> Any:
        """The client for the remote agent, rebuilt when its card gets old.

        A card that never expires means a subagent that changes url, capability
        or catalogue stays invisible until this process restarts.
        """
        fresh = "client" in cached and now() - cached["read_at"] < card_ttl_seconds
        if fresh:
            return cached["client"]

        try:
            card = await load_card()
        except Exception:
            if "client" not in cached:
                raise
            logger.warning(
                "The card of %s could not be read again: keeping the one in hand.",
                url,
                exc_info=True,
            )
            cached["read_at"] = now()
            return cached["client"]

        if not card.capabilities.streaming:
            logger.warning(
                "The card of %s does not declare streaming: answers will arrive whole.", url
            )
        cached["client"] = make_client(card)
        cached["read_at"] = now()
        await _catalogue_from_the_extended_card(cached["client"], card)
        return cached["client"]

    async def _catalogue_from_the_extended_card(remote: Any, card: AgentCard) -> None:
        """Asks for the extended view and puts the catalogue in the tool description.

        The public card says what the agent can do; which documents it has
        indexed it only tells whoever authenticates. For the model that is the
        difference between asking blindly and knowing what there is to ask for.
        """
        token = get_settings().knowledge_service_token
        if not token or not card.capabilities.extended_agent_card:
            return
        extended = await remote.extended_card(token)
        if extended is None:
            return
        catalogue = next((s.description for s in extended.skills if s.id == "catalogue"), "")
        if not catalogue:
            return
        logger.info("Extended card of the knowledge agent: %s", catalogue)
        ask_knowledge.description = f"""{DESCRIPTION}

{catalogue}"""

    @tool
    async def ask_knowledge(
        question: Annotated[str, "The question to pass to the knowledge agent, self-contained"],
    ) -> Content:
        """Queries the knowledge base agent about a topic."""
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
        question_from_the_subagent = ""

        try:
            remote = await client()
            async with subagent_run("knowledge", question):
                progress: Progress | None = None
                try:
                    async with asyncio.timeout(settings.subagent_wait_seconds):
                        async for progress in remote.ask(question, webhook=webhook):
                            task_id = progress.task_id or task_id
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
            logger.error("Knowledge agent unreachable for '%s'.", question, exc_info=True)
            return Content.from_text(
                "The knowledge agent did not answer: go on with what you know, "
                "stating that this part is not verified."
            )

        answer = "".join(pieces).strip()
        briefing = next(
            (a.data for a in briefings if a.data and a.data.get("component") == "briefing"), None
        )
        logger.info(
            "Knowledge agent on '%s': task %s, states %s, %d artifacts in %.2fs, %d characters.",
            question,
            task_id[:8] or "?",
            " -> ".join(states) or "none",
            artifact_count,
            time.monotonic() - start,
            len(answer),
        )

        if late:
            logger.info(
                "Task %s of the subagent outlasts the wait: going on, the outcome will arrive by webhook.",
                task_id[:8] or "?",
            )
            partial = f" So far it said: {answer}" if answer else ""
            return Content.from_text(
                "The knowledge agent is still working and I did not wait any longer."
                + partial
                + " The outcome will arrive as a notification and will be available next turn:"
                " tell the user that instead of inventing the answer."
            )

        if question_from_the_subagent:
            logger.info(
                "Task %s is waiting for a clarification: %s",
                task_id[:8] or "?",
                question_from_the_subagent,
            )
            return state_update(
                text=(
                    f"The knowledge agent stopped and asks: {question_from_the_subagent}\n"
                    "Pass the question on to the user instead of answering in their place. "
                    "When the user answers, use 'answer_subagent'."
                ),
                state={
                    "subagent_pending": {
                        "task_id": task_id,
                        "agent": "knowledge",
                        "question": question_from_the_subagent,
                        "request": question,
                    }
                },
            )
        if not answer:
            return Content.from_text(
                f"The knowledge agent produced no answer about '{question}'."
            )
        if briefing is None:
            return Content.from_text(answer)

        artifact_id = f"kb_{task_id[:8] or uuid4().hex[:8]}"
        return state_update(
            text=answer,
            tool_result={
                "component": "briefing",
                "id": artifact_id,
                "agent": "knowledge",
                "question": str(briefing.get("question", question)),
                "documents": [str(d) for d in briefing.get("documents", [])],
                "summary": str(briefing.get("summary", answer)),
            },
            state={
                "artifacts": [
                    {
                        "id": artifact_id,
                        "component": "briefing",
                        "title": f"knowledge: {question[:60]}",
                    }
                ]
            },
        )

    @tool
    async def answer_subagent(
        answer: Annotated[str, "The user's answer to the clarification the subagent asked for"],
    ) -> Content:
        """Resumes the subagent that asked for a clarification.

        Use it when the user answers a question the knowledge agent passed up to
        you: the conversation with the subagent resumes from the same task, not
        from scratch.
        """
        waiting = pending_of_run()
        if not waiting or not waiting.get("task_id"):
            return Content.from_text(
                "No subagent is waiting for an answer: query it from scratch if needed."
            )

        task_id = str(waiting["task_id"])
        pieces: list[str] = []
        briefings: list = []
        try:
            remote = await client()
            async with subagent_run("knowledge", answer):
                async for progress in remote.ask(answer, task_id=task_id):
                    if progress.text:
                        pieces.append(progress.text)
                    if progress.artifact:
                        briefings.append(progress.artifact)
                        if progress.artifact.text:
                            pieces.append(progress.artifact.text)
        except Exception:
            logger.error("Resuming task %s failed.", task_id[:8], exc_info=True)
            return Content.from_text(
                "I could not resume the subagent: tell the user."
            )

        text = "".join(pieces).strip()
        logger.info("Task %s resumed: %d characters.", task_id[:8], len(text))
        return state_update(
            text=text or "The subagent added nothing after the clarification.",
            state={"subagent_pending": {}},
        )

    ask_knowledge.description = DESCRIPTION
    return [ask_knowledge, answer_subagent]
