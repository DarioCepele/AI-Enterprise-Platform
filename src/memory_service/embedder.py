"""Da testo a vettore: l'unico pezzo che sa cosa vuol dire "simile"."""
from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """Trasforma testi in vettori. Un solo giro per lista, non uno per testo."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimensions(self) -> int: ...


class OpenAICompatibleEmbedder:
    """Embeddings da un endpoint compatibile OpenAI.

    Lo stesso provider dell'agente serve anche questi: OpenRouter espone
    `/embeddings`, verificato prima di scriverci sopra. Un modello di
    embedding e' piccolo e costa poco: qui non serve quello che risponde.
    """

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
        """Quanto e' lungo un vettore di questo modello.

        Si scopre alla prima chiamata invece di dichiararlo: un numero scritto
        a mano e diverso da quello vero fallisce solo al primo inserimento,
        cioe' lontano da dove e' stato scritto.
        """
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
