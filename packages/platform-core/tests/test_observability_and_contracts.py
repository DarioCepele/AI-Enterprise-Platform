"""Structured logs carry the work they belong to; contracts load from one place."""

from __future__ import annotations

import json
import logging

import pytest

from platform_core import observability
from platform_core.testing import contracts


def test_a_json_line_carries_the_context_and_the_extras():
    formatter = observability.JsonFormatter(
        "svc", context=lambda: {"thread_id": "t1", "x": ""}
    )
    record = logging.LogRecord(
        "app.tool", logging.INFO, __file__, 1, "done %s", ("ok",), None
    )
    record.step = "plan"

    line = json.loads(formatter.format(record))

    assert line["service"] == "svc"
    assert line["message"] == "done ok"
    assert line["thread_id"] == "t1"
    assert "x" not in line  # empty context values are not noise in every line
    assert line["step"] == "plan"


def test_without_a_collector_telemetry_stays_off(monkeypatch):
    monkeypatch.delenv(observability.ENDPOINT_VARIABLE, raising=False)
    assert observability.configure_telemetry("svc") is False


def test_contracts_load_from_the_configured_folder(tmp_path, monkeypatch):
    folder = tmp_path / "contracts" / "a2a"
    folder.mkdir(parents=True)
    (folder / "briefing.json").write_text(
        json.dumps(
            {
                "contract": "a2a/briefing",
                "version": 1,
                "produced_by": ["knowledge-agent"],
                "consumed_by": ["master-agent"],
                "sample": {"summary": "s", "documents": ["go"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path / "contracts"))

    contracts.assert_shape("a2a/briefing", {"summary": "x", "documents": []})
    with pytest.raises(AssertionError, match="master-agent"):
        contracts.assert_shape("a2a/briefing", {"summary": "x"})
    with pytest.raises(FileNotFoundError, match="a2a/briefing"):
        contracts.load("a2a/unknown")
