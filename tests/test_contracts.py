"""What this agent puts on the wire, checked against the shared contract."""
from __future__ import annotations

from google.protobuf.json_format import MessageToDict

from contracts import assert_shape, load

from analysis.executor import assessment


def test_the_assessment_artifact_matches_the_contract():
    parts = assessment(
        "Quale opzione conviene, a parita' di costo?",
        "La serie e' dominata da un valore isolato.",
        [
            {
                "tool": "measure",
                "arguments": {"values": "[12, 15, 11, 14, 98]"},
                "result": {
                    "count": 5,
                    "min": 11,
                    "max": 98,
                    "mean": 30.0,
                    "median": 14,
                    "spread": 33.7047,
                    "outliers": [98],
                    "thin": False,
                },
            }
        ],
    )

    data = MessageToDict(next(part.data for part in parts if part.HasField("data")))
    assert_shape("a2a/assessment", data)


def test_the_contract_names_this_repository_as_the_producer():
    # If this ever fails, the contract moved and the failure message above would
    # send whoever broke it to the wrong repository.
    assert "demo-analysis-agent" in load("a2a/assessment")["produced_by"]
