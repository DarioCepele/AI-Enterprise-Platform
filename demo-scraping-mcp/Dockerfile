# The official Playwright image, not a hand-picked list of apt packages: it is
# Microsoft's own, tested pairing of an OS and exactly the libraries headless
# Chromium needs. Pinned by digest, like every other base image here.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0

COPY --from=ghcr.io/astral-sh/uv:0.10.3@sha256:7a88d4c4e6f44200575000638453a5a381db0ae31ad5c3a51b14f8687c9d93a3 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY --from=platform-core pyproject.toml /packages/platform-core/pyproject.toml
COPY --from=platform-core src /packages/platform-core/src

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Crawl4AI pins its own Playwright version, which may not match the browser
# baked into the base image: this makes sure the right one is there (a no-op
# when it already is), installed as root where every user can read it.
RUN /app/.venv/bin/playwright install --with-deps chromium

# A browser that renders pages chosen by strangers must not run as root: the
# image's own unprivileged user. Chromium's sandbox needs privileges a
# container does not grant, so the sandbox is the container -- and, around it,
# the network the orchestrator gives this service, with no route to the
# platform's other services.
USER pwuser

EXPOSE 8600

CMD ["/app/.venv/bin/python", "-m", "scraping_mcp"]
