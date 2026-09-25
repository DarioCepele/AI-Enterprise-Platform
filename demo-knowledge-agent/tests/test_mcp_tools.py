"""`KNOWLEDGE_MCP_SERVERS` config parsing, and `build_mcp_tools` turning it
into `MCPStreamableHTTPTool` instances.

Connecting to a real MCP server is `agent_framework`'s own job (`Agent`
itself opens the session lazily, on first use), not retested here.
"""
from __future__ import annotations

import pytest
from agent_framework import MCPStreamableHTTPTool

from knowledge.mcp_tools import MCPServerConfig, build_mcp_tools, mcp_servers_from_env


def test_no_servers_configured_means_no_tools():
    assert mcp_servers_from_env(raw="") == ()
    assert build_mcp_tools(()) == []


def test_parses_a_json_list():
    servers = mcp_servers_from_env(
        raw='[{"name":"docs","url":"http://mcp-docs:9000/mcp"},'
        '{"name":"search","url":"http://mcp-search:9100/mcp","token":"secret"}]'
    )

    assert [s.name for s in servers] == ["docs", "search"]
    assert servers[1].token == "secret"  # noqa: S105


def test_a_name_unusable_as_a_tool_prefix_is_rejected():
    with pytest.raises(ValueError, match="not usable as a tool prefix"):
        mcp_servers_from_env(raw='[{"name":"!!!","url":"http://mcp:9000/mcp"}]')


def test_build_mcp_tools_prefixes_by_server_name():
    configs = (
        MCPServerConfig(name="docs", url="http://mcp-docs:9000/mcp"),
        MCPServerConfig(name="search", url="http://mcp-search:9100/mcp"),
    )

    tools = build_mcp_tools(configs)

    assert [t.tool_name_prefix for t in tools] == ["docs", "search"]
    assert all(isinstance(t, MCPStreamableHTTPTool) for t in tools)


def test_a_token_becomes_a_bearer_header():
    config = MCPServerConfig(
        name="docs", url="http://mcp-docs:9000/mcp", token="secret"  # noqa: S106
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
