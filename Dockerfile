# Stessa base del master agent: un solo modo di costruire immagini Python qui.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Prima i soli manifest: il layer delle dipendenze si invalida quando cambiano
# le dipendenze, non a ogni modifica del codice.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8100

# host 0.0.0.0: dentro un container 127.0.0.1 non e' raggiungibile da fuori.
CMD ["uv", "run", "--no-sync", "uvicorn", "memory_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
