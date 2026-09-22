"""The tools an agent draws from external MCP servers.

Same shape as `subagent_tools.py`'s A2A delegation, a different protocol:
whoever forks this template points the agent at MCP servers instead of (or
alongside) writing first-party tools. `agent_framework`'s `MCPStreamableHTTPTool`
already implements the MCP client side (connect, discover, call) -- this
module only turns configuration into instances of it. Streamable HTTP, not
stdio: every other remote dependency in this platform is a service reachable
over HTTP, and a stdio server would mean spawning a local subprocess inside
the container, a different operational model from everything else here.

Connecting is lazy: `Agent` itself opens the MCP session on first use (see
`agent_framework`'s `Agent._run_impl`, which calls `enter_async_context` on
any `MCPTool` found in its tool list that is not connected yet) and keeps it
open for the process lifetime, along with every other tool -- nothing here
manages that lifecycle by hand.
"""
from __future__ import annotations

from collections.abc import Callable

from agent_framework import MCPStreamableHTTPTool

from ..config import MCPServerConfig


def build_mcp_tools(
    configs: tuple[MCPServerConfig, ...],
) -> list[MCPStreamableHTTPTool]:
    """One `MCPStreamableHTTPTool` per configured server.

    `tool_name_prefix=config.name` keeps two servers that happen to expose a
    same-named tool (e.g. both offering `search`) from colliding once their
    tools are merged into the agent's own list.
    """
    return [
        MCPStreamableHTTPTool(
            name=config.name,
            url=config.url,
            tool_name_prefix=config.name,
            allowed_tools=config.allowed_tools or None,
            header_provider=_bearer_header(config.token) if config.token else None,
        )
        for config in configs
    ]


def _bearer_header(token: str) -> Callable[[dict[str, str]], dict[str, str]]:
    """A static `header_provider`: same token on every call, regardless of
    the tool-invocation kwargs `MCPStreamableHTTPTool` passes it."""

    def add_bearer(_kwargs: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return add_bearer
