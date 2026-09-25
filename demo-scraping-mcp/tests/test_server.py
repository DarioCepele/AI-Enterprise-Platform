"""`fetch_url` is registered on the MCP server with a real input schema --
`test_scraper.py` already covers what it does once called."""
from __future__ import annotations

from scraping_mcp.server import mcp


async def test_fetch_url_is_registered_with_a_url_parameter():
    tools = await mcp.list_tools()

    [fetch_url] = [t for t in tools if t.name == "fetch_url"]
    assert "url" in fetch_url.input_schema["properties"]
