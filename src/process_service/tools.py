"""The tools a process step can call.

Two of them write into `side_effects`, which stands in for the thing a real
process would do outside itself -- send, charge, create. It exists so that
"this must not happen twice" is a claim a test can check, instead of a comment.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Tool = Callable[[dict[str, Any]], dict[str, Any]]

_TOOLS: dict[str, Tool] = {}


def tool(name: str) -> Callable[[Tool], Tool]:
    def register(function: Tool) -> Tool:
        _TOOLS[name] = function
        return function

    return register


def get_tool(name: str) -> Tool:
    try:
        return _TOOLS[name]
    except KeyError:
        raise KeyError(
            f"tool '{name}' is not registered. Known: {', '.join(sorted(_TOOLS)) or 'none'}"
        ) from None


def registered() -> list[str]:
    return sorted(_TOOLS)


@tool("collect_request")
def collect_request(context: dict[str, Any]) -> dict[str, Any]:
    """Reads what the instance was started with. No effect outside."""
    return {"amount": context.get("amount"), "request_id": context.get("request_id")}


@tool("apply_request")
def apply_request(context: dict[str, Any]) -> dict[str, Any]:
    """The step that must not happen twice. The effect is recorded, not simulated."""
    return {"applied": True, "request_id": context.get("request_id")}


@tool("revert_request")
def revert_request(context: dict[str, Any]) -> dict[str, Any]:
    """What undoes the one above, when something after it fails."""
    return {"reverted": True, "request_id": context.get("request_id")}


@tool("notify_requester")
def notify_requester(context: dict[str, Any]) -> dict[str, Any]:
    return {"notified": context.get("request_id")}
