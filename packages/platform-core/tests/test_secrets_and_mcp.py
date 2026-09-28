"""Secrets fail closed; MCP configuration is validated once, the same everywhere."""

from __future__ import annotations

import logging

import pytest

from platform_core.mcp import parse_mcp_servers, tool_identifier
from platform_core.secrets import MissingSecretError, require_secret


def test_a_required_secret_that_is_empty_stops_the_service():
    with pytest.raises(MissingSecretError, match="MASTER_PUSH_SECRET"):
        require_secret("MASTER_PUSH_SECRET", "  ")


def test_an_optional_empty_secret_is_just_absent():
    assert require_secret("X", "", required=False) == ""


def test_a_short_secret_is_refused():
    with pytest.raises(MissingSecretError, match="shorter"):
        require_secret("X", "short")


def test_a_placeholder_is_accepted_and_said_out_loud(caplog):
    with caplog.at_level(logging.WARNING):
        assert (
            require_secret("X", "cambiami-segreto-locale") == "cambiami-segreto-locale"
        )
    assert "placeholder" in caplog.text


def test_mcp_servers_are_parsed_and_named_safely():
    [server] = parse_mcp_servers(
        '[{"name": "Web Scraping", "url": "http://scraping-mcp:8600/mcp", '
        '"allowed_tools": ["fetch_url"], "approval": "always"}]'
    )
    assert server.name == "web_scraping"
    assert server.allowed_tools == ("fetch_url",)
    assert server.approval == "always"


def test_no_mcp_servers_is_not_an_error():
    assert parse_mcp_servers("") == ()
    assert parse_mcp_servers("   ") == ()


def test_a_name_that_cannot_be_a_tool_name_is_refused():
    with pytest.raises(ValueError, match="not usable"):
        tool_identifier("9lives")
