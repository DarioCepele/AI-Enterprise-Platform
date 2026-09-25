"""What this agent produces and consumes, checked against the shared contracts."""
from __future__ import annotations

import json

import httpx
import pytest
from a2a.types import AgentCapabilities, AgentCard
from contracts import assert_shape, load, sample

from demo.a2a.client import Artifact, Progress
from demo.config import SubagentConfig
from demo.logging_bridge import LogCollector, SharedLogStream
from demo.memory.remote_store import MemoryServiceSnapshotStore
from demo.plan import PlanStore
from demo.tools.memory_tools import build_memory_tools
from demo.tools.subagent_tools import build_subagent_tools
from demo.tools.ui_tools import DISPLAY_KEY, STATE_KEY, ui_table

STEPS = [
    {
        "id": 1,
        "title": "Ask the knowledge base",
        "detail": "",
        "source": "ask_knowledge",
    }
]


class RemoteReturningTheBriefing:
    """A knowledge agent that answers exactly what the A2A contract declares."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, text, task_id=None, context_id=None, webhook=None):
        self.asked.append(text)
        progress = Progress(task_id="t-1", state="working", raw_state=2)
        progress.artifact = Artifact(
            artifact_id="a1",
            name="briefing",
            description="",
            text=sample("a2a/briefing")["summary"],
            data=sample("a2a/briefing"),
        )
        yield progress
        yield Progress(task_id="t-1", state="completed", raw_state=3)


def knowledge_tool(remote):
    async def card(config):
        return AgentCard(
            name="knowledge", capabilities=AgentCapabilities(streaming=True)
        )

    return build_subagent_tools(
        [SubagentConfig(name="knowledge", url="http://kb:8200/")],
        card_loader=card,
        client_factory=lambda config, _card: remote,
    )[0]


@pytest.mark.asyncio
async def test_the_briefing_of_the_subagent_is_read_as_the_contract_declares():
    remote = RemoteReturningTheBriefing()

    result = await knowledge_tool(remote).func(question="How does Go do typing?")

    payload = json.loads(result.additional_properties[DISPLAY_KEY])
    assert_shape("agui/tool-result-briefing", payload)


def test_the_table_payload_matches_the_contract():
    content = ui_table.func(
        title="Go vs Rust",
        columns=["Topic", "Go", "Rust"],
        rows=[["Concurrency", "a", "b"]],
    )

    assert_shape(
        "agui/tool-result-ui-table",
        json.loads(content.additional_properties[DISPLAY_KEY]),
    )


def test_the_plan_written_into_the_shared_state_matches_the_contract():
    store = PlanStore()
    store.write(STEPS)

    assert_shape(
        "agui/shared-state", {**sample("agui/shared-state"), "plan": store.snapshot()}
    )


def test_the_artifact_entry_of_the_shared_state_matches_the_contract():
    state = ui_table.func(
        title="Go vs Rust", columns=["A"], rows=[["1"]]
    ).additional_properties[STATE_KEY]

    expected = sample("agui/shared-state")["artifacts"][0]
    assert sorted(state["artifacts"][0]) == sorted(expected), (
        load("agui/shared-state")["consumed_by"]
    )


@pytest.mark.asyncio
async def test_the_pending_subagent_written_into_the_state_matches_the_contract():
    class RemoteThatAsks(RemoteReturningTheBriefing):
        async def ask(self, text, task_id=None, context_id=None, webhook=None):
            progress = Progress(
                task_id="t-1", state="waiting for an answer", raw_state=6
            )
            progress.question = "Which language?"
            yield progress

    result = await knowledge_tool(RemoteThatAsks()).func(
        question="how does concurrency work?"
    )

    pending = result.additional_properties[STATE_KEY]["subagent_pending"]
    assert_shape(
        "agui/shared-state",
        {**sample("agui/shared-state"), "subagent_pending": pending},
    )


def memory_talking_with(payload: dict, status: int = 200):
    def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://memory")


@pytest.mark.asyncio
async def test_the_snapshot_of_the_memory_is_read_as_the_contract_declares():
    payload = sample("memory/snapshot")
    store = MemoryServiceSnapshotStore(
        "http://memory", client=memory_talking_with(payload)
    )

    snapshot = await store.get(scope="tenant-a", thread_id="t1")

    assert snapshot.messages == payload["messages"]
    assert snapshot.state == payload["state"]
    assert snapshot.session_state == payload["session_state"]


@pytest.mark.asyncio
async def test_the_pruning_report_of_the_memory_is_read_as_the_contract_declares(
    caplog,
):
    import logging

    payload = sample("memory/snapshot")
    store = MemoryServiceSnapshotStore(
        "http://memory", client=memory_talking_with(payload)
    )

    with caplog.at_level(logging.INFO, logger="demo.memory.remote_store"):
        await store.get(scope="tenant-a", thread_id="t1")

    curation = payload["curation"]
    assert f"{curation['reasoning_removed']} reasonings removed" in caplog.text
    assert f"{curation['results_emptied']} results emptied" in caplog.text


@pytest.mark.asyncio
async def test_the_write_counters_of_the_memory_are_read_as_the_contract_declares(
    caplog,
):
    import logging

    counters = sample("memory/write-results")
    store = MemoryServiceSnapshotStore(
        "http://memory", client=memory_talking_with(counters["forget_thread"])
    )

    removed = await store.delete(scope="tenant-a", thread_id="t1")

    assert removed is True

    from agent_framework.ag_ui import AGUIThreadSnapshot

    saving = MemoryServiceSnapshotStore(
        "http://memory", client=memory_talking_with(counters["save_snapshot"])
    )
    with caplog.at_level(logging.INFO, logger="demo.memory.remote_store"):
        await saving.save(
            scope="tenant-a",
            thread_id="t1",
            snapshot=AGUIThreadSnapshot(messages=[], state={}),
        )

    assert f"{counters['save_snapshot']['new_turns']} new turns" in caplog.text


@pytest.mark.asyncio
async def test_the_memories_found_are_read_as_the_contract_declares():
    payload = sample("memory/search")
    tool = build_memory_tools(
        "http://memory", "tenant-a", client=memory_talking_with(payload)
    )[0]

    answer = await tool.func(query="who was the contact?")

    for memory in payload["memories"]:
        assert memory["text"] in answer.text
        assert f"{memory['similarity']:.2f}" in answer.text


def test_the_contracts_name_this_repository_on_both_sides():
    assert "demo-master-agent" in load("a2a/briefing")["consumed_by"]
    assert "demo-master-agent" in load("agui/shared-state")["produced_by"]
    assert "demo-master-agent" in load("memory/search")["consumed_by"]


def test_the_logs_page_of_this_replica_matches_the_contract():
    collector = LogCollector()
    collector.append({"ts": "t", "level": "INFO", "source": "tools", "message": "one"})

    assert_shape("agui/logs-page", collector.since(""))


@pytest.mark.asyncio
async def test_the_logs_page_of_the_shared_stream_matches_the_contract():
    """The shared page has the same shape as the local one, or the tab breaks."""

    class Rows:
        """A database that answers the two queries the stream makes."""

        def __init__(self) -> None:
            self.entry = sample("agui/logs-page")["entries"][0]

        async def execute(self, query, parameters=None):
            self._last = query
            return self

        async def fetchall(self):
            return [
                (
                    self.entry["seq"],
                    self.entry["ts"],
                    self.entry["level"],
                    self.entry["source"],
                    self.entry["message"],
                )
            ]

        async def fetchone(self):
            return (self.entry["seq"],)

        def cursor(self):
            return self

        def connection(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    collector = LogCollector()
    stream = SharedLogStream(Rows())
    stream.attach(collector)
    collector.append({"ts": "t", "level": "INFO", "source": "tools", "message": "one"})
    await stream.flush()

    assert_shape("agui/logs-page", await stream.since(""))
