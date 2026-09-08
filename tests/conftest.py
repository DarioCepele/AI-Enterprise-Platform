"""Shared fixtures using real Mongo and Redis from demo-infra. Integration tests verify single-document atomicity and unique indexes that mocks cannot reproduce."""
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
    reason="MEMORY_MONGO_URI and MEMORY_REDIS_URI are required in .env",
)


@pytest.fixture
def scope() -> str:
    """Allocate a distinct scope for each test to avoid interference."""
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
