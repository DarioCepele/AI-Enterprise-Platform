"""A minimal, self-hosted MCP server: fetches a URL and returns it as Markdown.

Built first-party rather than adopting one of the community MCP wrappers
around Crawl4AI: this module is the entire MCP-facing surface, kept small so it
stays reviewable. It is also the platform's example of how an MCP server is
written -- which is why it treats every URL as untrusted input
(`scraper.py`) instead of fetching whatever it is told.
"""

from __future__ import annotations

from mcp.server import MCPServer

from .config import get_settings
from .scraper import fetch_markdown

mcp = MCPServer("scraping")


@mcp.tool()
async def fetch_url(url: str) -> str:
    """Fetches a public web page and returns its content as clean Markdown.

    Renders the page in a real browser first, so it also works on pages that
    need JavaScript. Addresses inside private networks are refused, and long
    pages come back truncated.
    """
    settings = get_settings()
    return await fetch_markdown(
        url,
        settings.policy(),
        max_chars=settings.max_chars,
        timeout_seconds=settings.timeout_seconds,
    )
