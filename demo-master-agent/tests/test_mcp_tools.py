"""`MASTER_MCP_SERVERS` parsing, and the tools it becomes.

The same shape `test_subagents.py` exercises for `MASTER_SUBAGENTS`, a
different protocol. Connecting to a real MCP server is Agent Framework's own
job (the agent opens the session lazily, on first use), not retested here.
"""

from __future__ import annotations

import pytest
from agent_framework import MCPStreamableHTTPTool
from platform_core.mcp import build_mcp_tools
from pydantic import ValidationError

from master_agent.config import MCPServerConfig, get_settings


def test_no_servers_configured_means_no_tools(monkeypatch):
    monkeypatch.delenv("MASTER_MCP_SERVERS", raising=False)
    monkeypatch.delenv("MASTER_MCP_SERVERS", raising=False)

    assert get_settings().mcp_servers == ()
    assert build_mcp_tools(()) == []


def test_an_empty_list_is_not_an_error(monkeypatch):
    monkeypatch.setenv("MASTER_MCP_SERVERS", "")

    assert get_settings().mcp_servers == ()


def test_parses_a_json_list(monkeypatch):
    monkeypatch.setenv(
        "MASTER_MCP_SERVERS",
        '[{"name":"docs","url":"http://mcp-docs:9000/mcp"},'
        '{"name":"search","url":"http://mcp-search:9100/mcp","token":"secret"}]',
    )

    servers = get_settings().mcp_servers

    assert [s.name for s in servers] == ["docs", "search"]
    assert servers[1].token == "secret"  # noqa: S105


def test_the_old_variable_name_still_works(monkeypatch):
    monkeypatch.delenv("MASTER_MCP_SERVERS", raising=False)
    monkeypatch.setenv("MASTER_MCP_SERVERS", '[{"name":"docs","url":"http://x/mcp"}]')

    assert [s.name for s in get_settings().mcp_servers] == ["docs"]


def test_a_name_unusable_as_a_tool_prefix_is_rejected():
    with pytest.raises(ValidationError):
        MCPServerConfig(name="!!!", url="http://mcp:9000/mcp")


def test_build_mcp_tools_prefixes_by_server_name():
    configs = (
        MCPServerConfig(name="docs", url="http://mcp-docs:9000/mcp"),
        MCPServerConfig(name="search", url="http://mcp-search:9100/mcp"),
    )

    tools = build_mcp_tools(configs)

    assert [t.tool_name_prefix for t in tools] == ["docs", "search"]
    assert all(isinstance(t, MCPStreamableHTTPTool) for t in tools)


def test_an_allow_list_reaches_the_tool():
    config = MCPServerConfig(
        name="docs", url="http://mcp-docs:9000/mcp", allowed_tools=("search",)
    )

    [tool] = build_mcp_tools((config,))

    assert tool.allowed_tools == ("search",)


def test_a_server_can_require_a_person_before_any_of_its_tools_runs():
    config = MCPServerConfig(
        name="payments", url="http://mcp-pay:9000/mcp", approval="always"
    )

    [tool] = build_mcp_tools((config,))

    assert tool.approval_mode == "always_require"


def test_a_token_becomes_a_bearer_header():
    config = MCPServerConfig(
        name="docs",
        url="http://mcp-docs:9000/mcp",
        token="secret",  # noqa: S106
    )

    [tool] = build_mcp_tools((config,))

    # `MCPStreamableHTTPTool` keeps the provider on a private attribute; no
    # public accessor exists to assert this without one.
    assert tool._header_provider is not None
    assert tool._header_provider({})["Authorization"] == "Bearer secret"


def test_no_token_means_no_header_provider():
    config = MCPServerConfig(name="docs", url="http://mcp-docs:9000/mcp")

    [tool] = build_mcp_tools((config,))

    assert tool._header_provider is None
