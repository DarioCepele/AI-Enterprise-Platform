"""Runs the MCP server over streamable HTTP, the transport
`MCPStreamableHTTPTool` (the client this platform's agents use) speaks.
"""
from __future__ import annotations

import os

from .server import mcp

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        # A container-internal service reached only over this platform's own
        # docker network, the same posture every other service here takes.
        host="0.0.0.0",  # noqa: S104
        port=int(os.getenv("PORT", "8600")),
    )
