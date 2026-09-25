"""One URL in, its content as Markdown out.

A thin wrapper around Crawl4AI's `AsyncWebCrawler` -- the library itself
(63k+ stars, actively maintained), not one of the handful of low-adoption
community MCP wrappers built around it. `crawler_factory` exists so a test
can substitute a fake instead of launching a real headless browser, the
same substitutable-client shape `demo-master-agent`'s `vision.VisionClient`
already uses.
"""
from __future__ import annotations

from collections.abc import Callable

from crawl4ai import AsyncWebCrawler


def _real_crawler() -> AsyncWebCrawler:
    return AsyncWebCrawler()


async def fetch_markdown(
    url: str,
    crawler_factory: Callable[[], AsyncWebCrawler] = _real_crawler,
) -> str:
    """Renders `url` in a real (headless) browser and returns clean Markdown.

    A real browser, not a raw HTTP GET, so pages that need JavaScript to
    show their content still come back readable.
    """
    async with crawler_factory() as crawler:
        result = await crawler.arun(url)
        if not result.success:
            return f"Could not fetch {url}: {result.error_message}"
        # `result.markdown` is a plain string on older Crawl4AI versions and
        # a `MarkdownGenerationResult` (with `.raw_markdown` and a few other
        # variants) on newer ones -- `raw_markdown` when present, the value
        # itself otherwise, covers both without pinning an exact version.
        markdown = result.markdown
        return str(getattr(markdown, "raw_markdown", markdown))
