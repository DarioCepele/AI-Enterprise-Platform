"""`fetch_markdown`: public pages in, Markdown out, the network kept out.

Exercised against a fake crawler (no real browser, no network), the same
substitutable-client shape the master agent gives its vision client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from platform_core.urlguard import URLPolicy

from scraping_mcp.scraper import fetch_markdown, guard_hook, make_verdict

PUBLIC = "https://93.184.215.14/article"


class FakeStrategy:
    def __init__(self) -> None:
        self.hooks: dict[str, object] = {}

    def set_hook(self, name: str, hook: object) -> None:
        self.hooks[name] = hook


class FakeCrawler:
    """Records the URL it was asked to fetch and returns a canned result."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.requested_url: str | None = None
        self.crawler_strategy = FakeStrategy()

    async def __aenter__(self) -> FakeCrawler:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def arun(self, url: str, config: object = None) -> object:
        self.requested_url = url
        return self.result


def a_page(markdown: object, success: bool = True, error: str | None = None):
    return SimpleNamespace(success=success, error_message=error, markdown=markdown)


async def fetch(url: str, crawler: FakeCrawler, max_chars: int = 50_000) -> str:
    return await fetch_markdown(
        url,
        URLPolicy(),
        max_chars=max_chars,
        timeout_seconds=5,
        crawler_factory=lambda: crawler,
    )


async def test_returns_the_page_as_markdown():
    crawler = FakeCrawler(a_page("# Title\n\nBody."))

    assert await fetch(PUBLIC, crawler) == "# Title\n\nBody."
    assert crawler.requested_url == PUBLIC


async def test_every_request_of_the_page_goes_through_the_guard():
    crawler = FakeCrawler(a_page("x"))

    await fetch(PUBLIC, crawler)

    assert "on_page_context_created" in crawler.crawler_strategy.hooks


async def test_unwraps_a_markdown_generation_result():
    crawler = FakeCrawler(a_page(SimpleNamespace(raw_markdown="# Title")))

    assert await fetch(PUBLIC, crawler) == "# Title"


async def test_a_failed_fetch_says_why_instead_of_crashing():
    crawler = FakeCrawler(a_page(None, success=False, error="timed out"))

    text = await fetch(PUBLIC, crawler)

    assert PUBLIC in text and "timed out" in text


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "raw:<html><body>secret</body></html>",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8100/threads",
        "http://localhost:8300/instances",
    ],
)
async def test_the_network_and_the_filesystem_are_out_of_reach(url):
    crawler = FakeCrawler(a_page("should never be read"))

    text = await fetch(url, crawler)

    assert text.startswith("Refused to fetch")
    assert crawler.requested_url is None  # the browser never saw it


async def test_a_long_page_is_truncated_before_it_reaches_a_model():
    crawler = FakeCrawler(a_page("a" * 5000))

    text = await fetch(PUBLIC, crawler, max_chars=1000)

    assert text.startswith("a" * 1000)
    assert "truncated: 4000 more characters" in text


async def test_the_browser_boundary_blocks_requests_into_the_network():
    verdict = make_verdict(URLPolicy())

    assert await verdict("https://93.184.215.14/image.png") is True
    assert await verdict("http://10.0.0.5/admin") is False
    assert await verdict("http://127.0.0.1:8000/logs") is False
    assert await verdict("data:image/png;base64,AAAA") is True


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.outcome = ""

    async def continue_(self) -> None:
        self.outcome = "continued"

    async def abort(self, reason: str) -> None:
        self.outcome = f"aborted:{reason}"


class FakeContext:
    def __init__(self) -> None:
        self.route_handler = None
        self.socket_handler = None

    async def route(self, pattern: str, handler) -> None:
        self.route_handler = handler

    async def route_web_socket(self, pattern: str, handler) -> None:
        self.socket_handler = handler


async def test_the_hook_aborts_what_the_guard_refuses():
    context = FakeContext()
    await guard_hook(make_verdict(URLPolicy()))(object(), context=context)

    inside, outside = FakeRoute("http://10.1.1.1/"), FakeRoute(PUBLIC)
    await context.route_handler(inside)
    await context.route_handler(outside)

    assert inside.outcome == "aborted:blockedbyclient"
    assert outside.outcome == "continued"
