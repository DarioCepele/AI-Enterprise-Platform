# The official Playwright image, not a hand-picked list of apt packages: Crawl4AI's
# own docs call their Docker guidance "temporary" and admit they do not enumerate
# the system libraries headless Chromium needs -- this image is Microsoft's own,
# tested pairing of an OS and exactly those libraries.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# crawl4ai pins its own Playwright version, which may not exactly match the
# browser already baked into this base image -- this makes sure the right
# one is present regardless (a no-op download when it already is).
RUN uv run playwright install --with-deps chromium

EXPOSE 8600

# Every other service in this platform drops root before serving; this one
# does not, because Chromium's own sandbox wants either a non-root user with
# matching namespace permissions or --no-sandbox, and getting that right
# inside a container is its own can of worms. The accepted trade-off: this
# process only ever talks to this platform's own docker network, never
# receives traffic from outside it, and the container boundary is still
# there regardless of which user runs inside it.
CMD ["/app/.venv/bin/python", "-m", "scraping_mcp"]
