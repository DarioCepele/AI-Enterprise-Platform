"""Runs the MCP server over streamable HTTP, the transport the platform's
agents speak (`MCPStreamableHTTPTool`)."""

from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings
from platform_core.observability import configure_logging

from .config import get_settings
from .server import mcp

SERVICE_NAME = "scraping-mcp"

# A tool call carries a URL: a kilobyte is plenty, a megabyte is generous.
MAX_REQUEST_BYTES = 1_000_000


def main() -> None:
    settings = get_settings()
    configure_logging(SERVICE_NAME, as_json=settings.json_logs)
    mcp.run(
        transport="streamable-http",
        # Inside a container the service listens on every interface of its
        # own network namespace; what reaches it is decided by the network the
        # orchestrator puts it on, and by the Host check below.
        host="0.0.0.0",  # noqa: S104
        port=settings.port,
        max_request_body_size=MAX_REQUEST_BYTES,
        max_sessions=settings.max_sessions,
        # The MCP specification asks streamable-HTTP servers to validate Host
        # and Origin against DNS rebinding. The SDK turns this on by itself
        # only when bound to localhost; a container is not, so it is explicit.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_server_hosts(),
            allowed_origins=[],
        ),
    )


if __name__ == "__main__":
    main()
