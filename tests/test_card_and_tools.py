"""The subagent, its A2A identity card, and the arithmetic it does."""
from __future__ import annotations

import json

from a2a.client.client_factory import is_legacy_version

from analysis.agent import build_analysis_tools, scores_of, summary_of
from analysis.server import MEASURES, build_agent_card

CARD = build_agent_card("http://analysis:8400/")


def test_the_card_declares_streaming():
    assert CARD.capabilities.streaming is True


def test_the_card_declares_a_current_protocol_version():
    interface = CARD.supported_interfaces[0]

    assert interface.protocol_binding == "JSONRPC"
    assert is_legacy_version(interface.protocol_version) is False


def test_the_card_points_at_the_url_it_was_built_for():
    assert CARD.supported_interfaces[0].url == "http://analysis:8400/"


def test_the_card_says_what_this_agent_is_for():
    """Two subagents are only useful if a caller can tell them apart."""
    skill = CARD.skills[0]

    assert "numbers" in skill.tags
    assert CARD.name == "analysis"


def test_a_series_is_measured_from_whatever_shape_it_arrives_in():
    measure = build_analysis_tools()[0]

    as_json = json.loads(measure.func(values="[10, 12, 14]").text)
    as_prose = json.loads(measure.func(values="10, 12 14").text)

    assert as_json == as_prose
    assert as_json["mean"] == 12.0


def test_a_value_far_from_the_others_is_named():
    """The mean and the spread hide it; the median does not."""
    measured = summary_of([12, 15, 11, 14, 98])

    assert measured["outliers"] == [98]
    assert measured["max"] == 98


def test_a_short_series_says_that_it_is_short():
    assert summary_of([3, 4])["thin"] is True
    assert summary_of([3, 4, 5, 6, 7])["thin"] is False


def test_a_series_that_is_not_numbers_is_refused_with_the_reason():
    measure = build_analysis_tools()[0]

    answer = measure.func(values="ieri, oggi").text

    assert "not a number" in answer


def test_options_are_weighed_and_ranked():
    result = scores_of(
        {"a": {"cost": 3, "speed": 8}, "b": {"cost": 6, "speed": 5}},
        {"cost": 2, "speed": 1},
    )

    assert result["ranking"] == ["b", "a"]
    assert result["too_close"] is False


def test_a_ranking_nobody_should_act_on_says_so():
    """Two options within a hair of each other are a tie, not a winner."""
    result = scores_of({"a": {"x": 5.0}, "b": {"x": 5.02}}, {"x": 1})

    assert result["ranking"][0] == "b"
    assert result["too_close"] is True


def test_the_measures_the_card_promises_are_the_ones_computed():
    measured = summary_of([1, 2, 3])

    for name in ("count", "min", "max", "mean", "median", "spread", "outliers"):
        assert name in MEASURES or name == "outliers"
        assert name in measured
