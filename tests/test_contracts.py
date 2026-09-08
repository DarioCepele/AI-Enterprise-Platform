"""What this agent puts on the wire, checked against the shared contract."""
from __future__ import annotations

from google.protobuf.json_format import MessageToDict

from contracts import assert_shape, load
from knowledge.executor import briefing


def test_the_briefing_artifact_matches_the_contract():
    parts = briefing("How does Go do typing?", "Static typing.", ["go"])

    data = MessageToDict(next(part.data for part in parts if part.HasField("data")))
    assert_shape("a2a/briefing", data)


def test_the_contract_names_this_repository_as_the_producer():
    # If this ever fails, the contract moved and the failure message above would
    # send whoever broke it to the wrong repository.
    assert "demo-knowledge-agent" in load("a2a/briefing")["produced_by"]
