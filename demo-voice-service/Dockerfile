FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# Nothing in here needs root, and a process that never needs it should not have
# it: a container escape starts from whoever the process is.
RUN useradd --create-home --uid 10001 service && chown -R service:service /app
USER service

EXPOSE 8500

# The interpreter of the venv, not `uv run`: uv wants a writable cache,
# and a container that only serves HTTP has no business writing anywhere.
CMD ["/app/.venv/bin/uvicorn", "voice_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8500"]
