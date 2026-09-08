"""Fixture condivise.

I test di integrazione girano contro Mongo e Redis veri, quelli alzati da
`docker compose up -d mongo redis` in demo-infra. Non sono simulati apposta:
il modello a bucket senza transazioni si regge su garanzie del server --
atomicita' del singolo documento e indice unico -- che un finto non riproduce.
"""
from __future__ import annotations

import os
import uuid

import pytest
from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from redis.asyncio import Redis

from memory_service.stores.hot import HotTail
from memory_service.stores.mongo import MongoTranscripts, build_client

load_dotenv()

MONGO_URI = os.getenv("MEMORY_MONGO_URI")
REDIS_URI = os.getenv("MEMORY_REDIS_URI")

needs_backends = pytest.mark.skipif(
    not (MONGO_URI and REDIS_URI),
    reason="servono MEMORY_MONGO_URI e MEMORY_REDIS_URI nel .env",
)


@pytest.fixture
def scope() -> str:
    """Uno scope diverso per ogni test: nessuna interferenza fra esecuzioni."""
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def mongo_client() -> AsyncMongoClient:
    client = build_client(MONGO_URI or "mongodb://127.0.0.1:27017")
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def transcripts(mongo_client: AsyncMongoClient) -> MongoTranscripts:
    store = MongoTranscripts(mongo_client, "demo_memory_test", bucket_size=3)
    await store.ensure_indexes()
    return store


@pytest.fixture
async def redis_client() -> Redis:
    client = Redis.from_url(REDIS_URI or "redis://127.0.0.1:6379/0", decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def hot(redis_client: Redis) -> HotTail:
    return HotTail(redis_client, ttl_seconds=60, max_messages=10)
