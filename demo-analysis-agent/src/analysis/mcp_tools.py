"""The tools this agent draws from external MCP servers.

Same idea as `demo-master-agent`'s own `tools/mcp_tools.py` -- an MCP client
is conceptually the same pattern as this platform's A2A delegation to
subagents, a different protocol -- adapted to this repo's plain `os.getenv()`
style rather than pydantic-settings, since no `Settings` class exists here.
`agent_framework`'s `MCPStreamableHTTPTool` does the actual protocol work
(connect, discover, call); this module only turns configuration into
instances of it, and connects lazily on first use, the same way `Agent`
already handles every other tool.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from agent_framework import MCPStreamableHTTPTool

ENV_VARIABLE = "ANALYSIS_MCP_SERVERS"


@dataclass(frozen=True)
class MCPServerConfig:
    """An external MCP server this agent may draw tools from."""

    name: str
    url: str
    token: str = ""
    allowed_tools: tuple[str, ...] = ()


def _sanitize_name(value: str) -> str:
    """The name becomes part of a tool name, so it has to be one."""
    cleaned = "".join(
        char if char.isalnum() else "_" for char in value.strip().lower()
    )
    if not cleaned or not cleaned[0].isalpha():
        raise ValueError(f"MCP server name '{value}' is not usable as a tool prefix")
    return cleaned


def mcp_servers_from_env(raw: str | None = None) -> tuple[MCPServerConfig, ...]:
    """Parses `ANALYSIS_MCP_SERVERS`, a JSON list like
    `[{"name":"x","url":"http://...","token":"","allowed_tools":[]}]`.

    An empty or unset variable means no servers, not an error. `raw` is for
    tests; production callers read the environment.
    """
    text = (raw if raw is not None else os.getenv(ENV_VARIABLE, "")).strip()
    if not text:
        return ()
    entries = json.loads(text)
    return tuple(
        MCPServerConfig(
            name=_sanitize_name(entry["name"]),
            url=entry["url"],
            token=entry.get("token", ""),
            allowed_tools=tuple(entry.get("allowed_tools", ())),
        )
        for entry in entries
    )


def build_mcp_tools(
    configs: tuple[MCPServerConfig, ...],
) -> list[MCPStreamableHTTPTool]:
    """One `MCPStreamableHTTPTool` per configured server.

    `tool_name_prefix=config.name` keeps two servers that happen to expose a
    same-named tool from colliding once their tools are merged into the
    agent's own list.
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
