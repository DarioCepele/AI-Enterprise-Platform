"""`fetch_url` is registered on the MCP server with a real input schema."""

from __future__ import annotations

from scraping_mcp.config import Settings
from scraping_mcp.server import mcp


async def test_fetch_url_is_registered_with_a_url_parameter():
    tools = await mcp.list_tools()

    [fetch_url] = [t for t in tools if t.name == "fetch_url"]
    assert "url" in fetch_url.input_schema["properties"]


def test_private_targets_are_off_unless_the_operator_says_otherwise():
    assert Settings().policy().allow_private is False
    assert Settings(allow_private_targets=True).policy().allow_private is True


def test_the_server_answers_only_to_the_hosts_it_is_reached_by():
    hosts = Settings().allowed_server_hosts()
    assert "scraping-mcp:*" in hosts
