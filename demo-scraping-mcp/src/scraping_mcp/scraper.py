"""One URL in, its content as Markdown out -- and never a door into the network.

A thin wrapper around Crawl4AI's `AsyncWebCrawler`, the library itself rather
than one of the low-adoption community MCP wrappers built around it.

Rendering a page in a real browser is what makes JavaScript-heavy pages
readable, and also what makes a scraper dangerous: the page decides what else
the browser loads. Three rules keep it an outward-facing tool:

- the URL asked for must pass `platform_core.urlguard` (http/https only, a
  public address, no credentials in the URL) -- `file://` and Crawl4AI's `raw:`
  never reach the crawler;
- every request the browser makes afterwards -- redirects, images, scripts,
  XHR -- is checked by the same guard at the browser boundary and aborted when
  it points inside; WebSocket connections the same way;
- what comes back is bounded in time and in size before it reaches a model.

Egress rules at the network layer remain the second line: the compose file and
the manifests give this service no route to the platform's other services.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from platform_core.urlguard import BlockedURL, URLPolicy, check_url

logger = logging.getLogger(__name__)

Verdict = Callable[[str], Awaitable[bool]]


def _real_crawler() -> AsyncWebCrawler:
    return AsyncWebCrawler()


def make_verdict(policy: URLPolicy) -> Verdict:
    """Whether the browser may load a URL, cached per host for one page."""
    decided: dict[str, bool] = {}

    async def allowed(url: str) -> bool:
        if url.startswith(("data:", "blob:", "about:")):
            return True
        try:
            host = url.split("//", 1)[1].split("/", 1)[0].lower()
        except IndexError:
            return False
        if host in decided:
            return decided[host]
        try:
            await check_url(url, policy)
        except BlockedURL as error:
            logger.warning("Browser request blocked: %s", error)
            decided[host] = False
        else:
            decided[host] = True
        return decided[host]

    return allowed


def guard_hook(verdict: Verdict) -> Callable[..., Awaitable[None]]:
    """The Crawl4AI hook that puts the guard on every request of the page."""

    async def on_page_context_created(page: Any, context: Any = None, **_: Any) -> None:
        target = context if context is not None else page

        async def route(request_route: Any) -> None:
            if await verdict(request_route.request.url):
                await request_route.continue_()
            else:
                await request_route.abort("blockedbyclient")

        async def socket(web_socket: Any) -> None:
            if await verdict(web_socket.url.replace("ws", "http", 1)):
                web_socket.connect_to_server()
            else:
                await web_socket.close()

        await target.route("**/*", route)
        await target.route_web_socket("**/*", socket)

    return on_page_context_created


async def fetch_markdown(
    url: str,
    policy: URLPolicy,
    *,
    max_chars: int,
    timeout_seconds: float,
    crawler_factory: Callable[[], AsyncWebCrawler] = _real_crawler,
) -> str:
    """Renders `url` in a headless browser and returns clean Markdown, bounded."""
    try:
        await check_url(url, policy)
    except BlockedURL as error:
        logger.warning("Fetch refused: %s", error)
        return f"Refused to fetch {url}: {error}"

    try:
        async with asyncio.timeout(timeout_seconds), crawler_factory() as crawler:
            crawler.crawler_strategy.set_hook(
                "on_page_context_created", guard_hook(make_verdict(policy))
            )
            result = await crawler.arun(
                url, config=CrawlerRunConfig(page_timeout=int(timeout_seconds * 1000))
            )
    except TimeoutError:
        return f"Could not fetch {url}: it took longer than {timeout_seconds:g}s."

    if not result.success:
        return f"Could not fetch {url}: {result.error_message}"
    # `result.markdown` is a plain string on older Crawl4AI versions and a
    # `MarkdownGenerationResult` on newer ones: `raw_markdown` when present.
    markdown = str(getattr(result.markdown, "raw_markdown", result.markdown))
    if len(markdown) > max_chars:
        logger.info(
            "Page %s truncated: %d characters of %d.", url, max_chars, len(markdown)
        )
        markdown = (
            markdown[:max_chars]
            + f"\n\n[truncated: {len(markdown) - max_chars} more characters not shown]"
        )
    return markdown
