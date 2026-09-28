"""MCP servers from configuration, and what their presence changes in the prompt.

Connecting to a real MCP server is Agent Framework's own job (the agent opens
the session lazily, on first use), not retested here.
"""

from __future__ import annotations

import pytest
from agent_framework import MCPStreamableHTTPTool

from analysis.agent import build_analysis_agent, instructions_for
from analysis.config import Settings


def test_no_servers_configured_means_no_mcp_tools(offline_agent):
    names = [getattr(t, "name", "") for t in offline_agent.default_options["tools"]]
    assert names == ["measure", "compare"]


def test_configured_servers_become_prefixed_tools():
    settings = Settings(
        mcp_servers='[{"name":"docs","url":"http://mcp-docs:9000/mcp"},'
        '{"name":"search","url":"http://mcp-search:9100/mcp","token":"secret",'
        '"approval":"always"}]'
    )
    from platform_core.mcp import build_mcp_tools

    tools = build_mcp_tools(settings.mcp())

    assert [t.tool_name_prefix for t in tools] == ["docs", "search"]
    assert all(isinstance(t, MCPStreamableHTTPTool) for t in tools)
    assert tools[1]._header_provider({})["Authorization"] == "Bearer secret"


def test_a_name_unusable_as_a_tool_prefix_is_rejected():
    with pytest.raises(ValueError, match="not usable"):
        Settings(mcp_servers='[{"name":"!!!","url":"http://mcp:9000/mcp"}]').mcp()


def test_external_content_is_declared_untrusted_when_tools_bring_it_in():
    prompt = instructions_for("", uses_external_tools=True)

    assert "not instructions" in prompt
    assert "language the request is written in" in prompt


def test_without_external_tools_the_prompt_stays_about_the_numbers():
    prompt = instructions_for("Italian", uses_external_tools=False)

    assert "Answer in Italian." in prompt
    assert "strangers" not in prompt


def test_the_agent_uses_the_configured_language():
    agent = build_analysis_agent(
        chat_client=object(),  # type: ignore[arg-type]
        settings=Settings(language="Spanish"),
    )
    assert "Answer in Spanish." in agent.default_options["instructions"]
