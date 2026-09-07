# uv fornisce l'immagine con il gestore gia' dentro: niente pip, niente wheel a mano.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Prima i soli manifest: cosi' il layer delle dipendenze si invalida
# solo quando cambiano le dipendenze, non a ogni modifica del codice.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8000

# host 0.0.0.0: dentro un container 127.0.0.1 non e' raggiungibile da fuori.
# --no-sync usa l'ambiente costruito sopra senza installare dipendenze dev all'avvio.
CMD ["uv", "run", "--no-sync", "uvicorn", "demo.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
