"""Configurazione del servizio, letta dall'ambiente."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Impostazioni del servizio di memoria.

    Le connessioni arrivano per URI: il servizio non costruisce credenziali,
    le riceve gia' pronte da chi lo dispiega.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEMORY_", extra="ignore")

    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_database: str = "demo_memory"
    redis_uri: str = "redis://127.0.0.1:6379/0"

    # Quanti messaggi stanno in un documento bucket. 50 tiene il documento
    # lontanissimo dal limite di 16 MB anche con messaggi lunghi, e una lettura
    # della coda tocca uno o due documenti invece di centinaia.
    bucket_size: int = Field(default=50, ge=1, le=500)

    # La coda calda vive in Redis per questo tempo. Scaduta, si ricostruisce da
    # Mongo alla prima lettura: qui non c'e' mai l'unica copia di un dato.
    hot_tail_seconds: int = Field(default=1800, ge=1)
    hot_tail_messages: int = Field(default=100, ge=1)

    # Il modello che riassume. Vuoto = nessuna compattazione, dichiarata
    # all'avvio: i turni fuori finestra escono senza lasciare un riassunto.
    # E' un modello a parte da quello dell'agente: riassumere e' un lavoro
    # diverso dal rispondere, e puo' meritare un modello piu' piccolo.
    summary_model: str = ""
    summary_base_url: str = "https://openrouter.ai/api/v1"
    summary_api_key: str = ""

    # Potatura del contesto restituito. Non tocca cio' che e' scritto: si
    # conserva tutto e si restituisce il necessario.
    drop_reasoning: bool = True
    keep_tool_results: int = Field(default=4, ge=0)
    max_context_messages: int | None = Field(default=60, ge=1)
    # Quanti fatti duraturi entrano nel contesto. Senza un tetto, il contesto
    # di ogni run crescerebbe con tutto cio' che si e' mai saputo dell'utente.
    max_facts: int = Field(default=30, ge=0)


@lru_cache
def get_settings() -> Settings:
    """Una sola istanza per processo: la configurazione non cambia a caldo."""
    return Settings()
