"""A minimal, self-hosted MCP server: fetches a URL and returns it as Markdown.

Built first-party rather than adopting one of the handful of community MCP
wrappers around Crawl4AI (all with far less adoption/scrutiny than the
underlying library) -- this module is the entire MCP-facing surface, kept
deliberately small so it stays trustworthy to add to any agent's tool list.
Uses the official MCP Python SDK's server (streamable HTTP transport), the
same protocol `demo-master-agent`'s `MCPStreamableHTTPTool` client speaks.
"""
from __future__ import annotations

from mcp.server import MCPServer

from .scraper import fetch_markdown

mcp = MCPServer("scraping")


@mcp.tool()
async def fetch_url(url: str) -> str:
    """Fetches a web page and returns its content as clean Markdown.

    Renders the page in a real browser first, so it also works on pages
    that need JavaScript to show their content, not just static HTML.
    """
    return await fetch_markdown(url)
