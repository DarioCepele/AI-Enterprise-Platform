"""The upload store: a place for a video file to land before a URL can point at it.

AG-UI's video input part carries a URL, not the file -- this is where the file
lives in between, and only for a while. Two backends: a folder for a single
replica, and the shared database when there is one, so that the replica that
received the file and the one that analyzes it need not be the same.
"""

from __future__ import annotations

import os
import time

import pytest
from conftest import needs_postgres
from fastapi.testclient import TestClient

from master_agent.agents.master import build_master_agent
from master_agent.chat_clients.fake import FakeStreamingChatClient
from master_agent.server.app import create_app
from master_agent.server.uploads import (
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_UPLOAD_TTL_SECONDS,
    DiskUploadStore,
    PostgresUploadStore,
    upload_id_in,
)

VIDEO = {"content-type": "video/mp4"}


@pytest.fixture
def make_app(monkeypatch, tmp_path):
    """Builds the app with an explicit upload store, pointed at a temp folder."""
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")

    def _make(**store_kwargs):
        store = DiskUploadStore(directory=tmp_path, **store_kwargs)
        agent = build_master_agent(chat_client=FakeStreamingChatClient())
        return create_app(agent=agent, upload_store=store), store

    return _make


def upload_id_of(response) -> str:
    return response.json()["url"].rsplit("/", 1)[-1]


def test_an_uploaded_file_comes_back_identical(make_app):
    app, _store = make_app()

    with TestClient(app) as client:
        created = client.post("/uploads", content=b"tiny fake clip", headers=VIDEO)
        assert created.status_code == 200
        fetched = client.get(f"/uploads/{upload_id_of(created)}")

    assert fetched.status_code == 200
    assert fetched.content == b"tiny fake clip"
    assert fetched.headers["content-type"] == "video/mp4"
    assert fetched.headers["x-content-type-options"] == "nosniff"


def test_only_video_and_audio_are_accepted(make_app):
    app, _store = make_app()

    with TestClient(app) as client:
        page = client.post(
            "/uploads", content=b"<html>", headers={"content-type": "text/html"}
        )
        audio = client.post(
            "/uploads", content=b"RIFF", headers={"content-type": "audio/wav"}
        )

    # An attachment store is not a place to park arbitrary files for an hour.
    assert page.status_code == 415
    assert audio.status_code == 200


def test_the_response_url_is_rooted_at_the_base_url(make_app):
    app, _store = make_app()

    with TestClient(app, base_url="http://lab.test") as client:
        created = client.post("/uploads", content=b"x", headers=VIDEO)

    assert created.json()["url"].startswith("http://lab.test/uploads/")


def test_a_file_over_the_limit_is_refused(make_app):
    app, _store = make_app(max_bytes=10)

    with TestClient(app) as client:
        refused = client.post("/uploads", content=b"x" * 1000, headers=VIDEO)

    assert refused.status_code == 413
    assert "too large" in refused.text


def test_a_file_over_the_limit_leaves_nothing_behind(make_app, tmp_path):
    app, _store = make_app(max_bytes=10)

    with TestClient(app) as client:
        client.post("/uploads", content=b"x" * 1000, headers=VIDEO)

    assert list(tmp_path.iterdir()) == []


def test_a_missing_upload_is_a_404(make_app):
    app, _store = make_app()

    with TestClient(app) as client:
        response = client.get("/uploads/does-not-exist")

    assert response.status_code == 404


def test_an_upload_past_its_ttl_is_no_longer_reachable(make_app):
    now = {"t": 1_000.0}
    app, _store = make_app(ttl_seconds=60.0, clock=lambda: now["t"])

    with TestClient(app) as client:
        created = client.post("/uploads", content=b"clip", headers=VIDEO)
        now["t"] += 61.0  # past the TTL
        expired = client.get(f"/uploads/{upload_id_of(created)}")

    assert expired.status_code == 404


def test_the_lazy_sweep_deletes_the_expired_file_from_disk(make_app, tmp_path):
    now = {"t": 1_000.0}
    app, _store = make_app(ttl_seconds=60.0, clock=lambda: now["t"])

    with TestClient(app) as client:
        created = client.post("/uploads", content=b"clip", headers=VIDEO)
        upload_id = upload_id_of(created)
        assert (tmp_path / upload_id).exists()

        now["t"] += 61.0
        client.get(f"/uploads/{upload_id}")

    assert not (tmp_path / upload_id).exists()


def test_a_file_written_directly_and_backdated_past_the_ttl_is_swept(tmp_path):
    """The sweep works on whatever it finds on disk, not only on what this
    process itself wrote."""
    store = DiskUploadStore(directory=tmp_path, ttl_seconds=1.0)
    old_file = tmp_path / "already-here"
    old_file.write_bytes(b"leftover")
    old_time = time.time() - 10
    os.utime(old_file, (old_time, old_time))

    store.sweep()

    assert not old_file.exists()


def test_a_read_only_filesystem_does_not_stop_the_service_from_starting(
    monkeypatch, tmp_path
):
    # Nothing is created until the first upload: a container with a read-only
    # root and no upload volume still starts, and only uploads fail.
    missing = tmp_path / "not-created-yet"
    DiskUploadStore(directory=missing)

    assert not missing.exists()


def test_an_upload_url_is_recognised_whatever_host_it_names():
    upload = "0123456789abcdef0123456789abcdef"

    assert upload_id_in(f"http://localhost:8000/uploads/{upload}") == upload
    assert upload_id_in(f"https://agent.example/uploads/{upload}?x=1") == upload
    assert upload_id_in("https://agent.example/uploads/../etc/passwd") is None
    assert upload_id_in("https://videos.example/clip.mp4") is None


def test_the_default_ttl_and_limit_are_documented_and_reasonable():
    assert DEFAULT_MAX_UPLOAD_BYTES == 200 * 1024 * 1024
    assert DEFAULT_UPLOAD_TTL_SECONDS == 60 * 60


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


@needs_postgres
async def test_two_replicas_share_the_uploads_through_postgres(pool):
    """The replica that stored the file and the one that reads it differ."""
    from master_agent.migrations import run_migrations

    async with pool.connection() as connection:
        await run_migrations(connection)
    receiving = PostgresUploadStore(pool, max_bytes=10_000_000)
    analyzing = PostgresUploadStore(pool, max_bytes=10_000_000)
    big = b"a" * (1024 * 1024) + b"b" * 1000  # spans two chunks

    upload_id = await receiving.save(_chunks(big[:700_000], big[700_000:]), "video/mp4")
    content_type, chunks = await analyzing.open(upload_id)
    read = b"".join([chunk async for chunk in chunks])

    assert content_type == "video/mp4"
    assert read == big


@needs_postgres
async def test_an_oversized_upload_leaves_no_row_behind(pool):
    from master_agent.migrations import run_migrations

    async with pool.connection() as connection:
        await run_migrations(connection)
    store = PostgresUploadStore(pool, max_bytes=10)

    with pytest.raises(Exception, match="too large"):
        await store.save(_chunks(b"x" * 100), "video/mp4")

    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT count(*) FROM uploads WHERE size = 0")
        assert (await cursor.fetchone())[0] == 0
