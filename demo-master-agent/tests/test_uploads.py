"""The upload store: a place for a video file to land before a URL can point
at it. AG-UI's video input part carries a URL, not the file -- this is where
the file lives in between, and only for a while.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from demo.agents.master import build_master_agent
from demo.chat_clients.fake import FakeStreamingChatClient
from demo.server.app import create_app
from demo.server.uploads import UploadStore


@pytest.fixture
def make_app(monkeypatch, tmp_path):
    """Builds the app with an explicit upload store, pointed at a temp folder."""
    monkeypatch.setenv("DEMO_POSTGRES_DSN", "")

    def _make(**store_kwargs):
        store = UploadStore(directory=tmp_path, **store_kwargs)
        agent = build_master_agent(chat_client=FakeStreamingChatClient())
        return create_app(agent=agent, upload_store=store), store

    return _make


def test_an_uploaded_file_comes_back_identical(make_app):
    app, _store = make_app()

    with TestClient(app) as client:
        created = client.post("/uploads", content=b"tiny fake clip")
        assert created.status_code == 200
        url = created.json()["url"]
        upload_id = url.rsplit("/", 1)[-1]

        fetched = client.get(f"/uploads/{upload_id}")

    assert fetched.status_code == 200
    assert fetched.content == b"tiny fake clip"


def test_the_response_url_is_rooted_at_the_base_url(make_app):
    app, _store = make_app()

    with TestClient(app, base_url="http://lab.test") as client:
        created = client.post("/uploads", content=b"x")

    assert created.json()["url"].startswith("http://lab.test/uploads/")


def test_a_file_over_the_limit_is_refused(make_app):
    app, _store = make_app(max_bytes=10)

    with TestClient(app) as client:
        refused = client.post("/uploads", content=b"x" * 1000)

    assert refused.status_code == 413
    assert "too large" in refused.text


def test_a_file_over_the_limit_leaves_nothing_behind(make_app, tmp_path):
    app, _store = make_app(max_bytes=10)

    with TestClient(app) as client:
        client.post("/uploads", content=b"x" * 1000)

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
        created = client.post("/uploads", content=b"clip")
        upload_id = created.json()["url"].rsplit("/", 1)[-1]

        now["t"] += 61.0  # past the TTL

        expired = client.get(f"/uploads/{upload_id}")

    assert expired.status_code == 404


def test_the_lazy_sweep_deletes_the_expired_file_from_disk(make_app, tmp_path):
    now = {"t": 1_000.0}
    app, _store = make_app(ttl_seconds=60.0, clock=lambda: now["t"])

    with TestClient(app) as client:
        created = client.post("/uploads", content=b"clip")
        upload_id = created.json()["url"].rsplit("/", 1)[-1]
        assert (tmp_path / upload_id).exists()

        now["t"] += 61.0
        client.get(f"/uploads/{upload_id}")

    assert not (tmp_path / upload_id).exists()


def test_a_file_written_directly_and_backdated_past_the_ttl_is_swept(tmp_path):
    """The sweep works on whatever it finds on disk, not only on what this
    process itself wrote -- the scenario the contract asks to cover directly.
    """
    import os
    import time

    store = UploadStore(directory=tmp_path, ttl_seconds=1.0)
    old_file = tmp_path / "already-here"
    old_file.write_bytes(b"leftover")
    old_time = time.time() - 10
    os.utime(old_file, (old_time, old_time))

    store.sweep()

    assert not old_file.exists()


def test_the_default_ttl_and_limit_are_documented_and_reasonable():
    from demo.server.uploads import DEFAULT_MAX_UPLOAD_BYTES, DEFAULT_UPLOAD_TTL_SECONDS

    assert DEFAULT_MAX_UPLOAD_BYTES == 200 * 1024 * 1024
    assert DEFAULT_UPLOAD_TTL_SECONDS == 60 * 60
