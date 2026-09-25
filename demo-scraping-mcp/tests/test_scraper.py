"""`fetch_markdown`: one URL in, its content as Markdown out -- exercised
against a fake crawler (no real browser, no network), the same
substitutable-client shape `demo-master-agent`'s `FakeVisionClient` gives
its own external dependency.
"""
from __future__ import annotations

from types import SimpleNamespace

from scraping_mcp.scraper import fetch_markdown


class FakeCrawler:
    """Records the URL it was asked to fetch and returns a canned result."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.requested_url: str | None = None

    async def __aenter__(self) -> FakeCrawler:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def arun(self, url: str) -> object:
        self.requested_url = url
        return self.result


async def test_returns_the_page_as_markdown():
    crawler = FakeCrawler(
        SimpleNamespace(success=True, error_message=None, markdown="# Title\n\nBody.")
    )

    text = await fetch_markdown("https://example.com", crawler_factory=lambda: crawler)

    assert text == "# Title\n\nBody."
    assert crawler.requested_url == "https://example.com"


async def test_unwraps_a_markdown_generation_result():
    markdown_result = SimpleNamespace(raw_markdown="# Title\n\nBody.")
    crawler = FakeCrawler(
        SimpleNamespace(success=True, error_message=None, markdown=markdown_result)
    )

    text = await fetch_markdown("https://example.com", crawler_factory=lambda: crawler)

    assert text == "# Title\n\nBody."


async def test_a_failed_fetch_says_why_instead_of_crashing():
    crawler = FakeCrawler(
        SimpleNamespace(success=False, error_message="timed out", markdown=None)
    )

    text = await fetch_markdown("https://example.com", crawler_factory=lambda: crawler)

    assert "https://example.com" in text
    assert "timed out" in text
