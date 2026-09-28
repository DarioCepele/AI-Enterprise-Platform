# One image per service: the service folder, plus the platform's shared
# package from the named build context `platform-core` (see infra/compose.yaml
# and the images workflow). Base image pinned by digest: a tag moves, a digest
# does not.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

# Bytecode is compiled here, at build time: at run time the code is read-only.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=platform-core pyproject.toml /packages/platform-core/pyproject.toml
COPY --from=platform-core src /packages/platform-core/src

# Dependencies first, the service second: a code change does not reinstall
# the world. Not editable: the image carries installed packages, not links
# back to a source tree.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Nothing in here needs root, and a process that never needs it should not
# have it. The code stays owned by root: the service reads it, never rewrites
# it.
RUN useradd --create-home --uid 10001 service
USER service

EXPOSE 8000

# The interpreter of the venv, not `uv run`: uv wants a writable cache. The
# graceful-shutdown ceiling stays under Kubernetes' default 30s grace period,
# so streams in flight close cleanly before the kill.
CMD ["/app/.venv/bin/uvicorn", "master_agent.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "25"]
