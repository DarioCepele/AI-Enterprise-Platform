"""The tools an agent draws from external MCP servers, from configuration.

Agent Framework's `MCPStreamableHTTPTool` already implements the client side of
MCP -- connect, discover, call, over streamable HTTP -- and connects lazily on
first use. What is left is turning configuration into instances of it, the
same way for every agent of the platform.

Each server may require a person's approval before any of its tools runs
(`"approval": "always"`): a server somebody else wrote can do things this
platform cannot see, and deciding which ones need a human is configuration.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, field_validator


def tool_identifier(value: str, what: str = "name") -> str:
    """A name the model will read inside a tool name, so it has to be one."""
    cleaned = "".join(char if char.isalnum() else "_" for char in value.strip().lower())
    if not cleaned or not cleaned[0].isalpha():
        raise ValueError(f"{what} '{value}' is not usable inside a tool name")
    return cleaned


class MCPServerConfig(BaseModel):
    """An external MCP server an agent may draw tools from.

    `name` becomes the tool prefix, so two servers exposing a same-named tool
    (both offering `search`) do not collide.
    """

    name: str
    url: str
    token: str = ""
    allowed_tools: tuple[str, ...] = ()
    approval: Literal["never", "always"] = "never"
    timeout_seconds: int | None = None

    @field_validator("name", mode="after")
    @classmethod
    def _identifier(cls, value: str) -> str:
        return tool_identifier(value, "MCP server name")


def parse_mcp_servers(text: str) -> tuple[MCPServerConfig, ...]:
    """A JSON list of servers, or nothing: an empty value means none, not an error."""
    stripped = text.strip()
    if not stripped:
        return ()
    return tuple(
        MCPServerConfig.model_validate(entry) for entry in json.loads(stripped)
    )


def mcp_servers_from_env(variable: str) -> tuple[MCPServerConfig, ...]:
    return parse_mcp_servers(os.getenv(variable, ""))


def _bearer(token: str) -> Callable[[dict[str, Any]], dict[str, str]]:
    """A static header provider: the same token on every call."""

    def add_bearer(_kwargs: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return add_bearer


def build_mcp_tools(configs: tuple[MCPServerConfig, ...]) -> list[Any]:
    """One `MCPStreamableHTTPTool` per configured server. Requires the `maf` extra."""
    if not configs:
        return []
    from agent_framework import MCPStreamableHTTPTool

    return [
        MCPStreamableHTTPTool(
            name=config.name,
            url=config.url,
            tool_name_prefix=config.name,
            allowed_tools=config.allowed_tools or None,
            header_provider=_bearer(config.token) if config.token else None,
            approval_mode="always_require" if config.approval == "always" else None,
            request_timeout=config.timeout_seconds,
        )
        for config in configs
    ]
