"""Convert text into vectors for semantic similarity."""
from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """Embed a list of texts in a single batch."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimensions(self) -> int: ...


class OpenAICompatibleEmbedder:
    """Use an OpenAI-compatible embeddings endpoint, including OpenRouter. A dedicated small embedding model handles this independently of the chat model."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._model = model
        self._dimensions = 0
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    @property
    def dimensions(self) -> int:
        """Discover vector dimensions on the first call to avoid hard-coded sizes failing later during insertion."""
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings", json={"model": self._model, "input": texts}
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        vectors = [item["embedding"] for item in payload["data"]]
        if vectors:
            self._dimensions = len(vectors[0])
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()
