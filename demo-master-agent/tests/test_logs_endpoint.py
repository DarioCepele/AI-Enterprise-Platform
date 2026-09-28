import logging

import pytest
from fastapi.testclient import TestClient

from master_agent.agents.master import build_master_agent
from master_agent.chat_clients.fake import FakeStreamingChatClient
from master_agent.logging_bridge import LogCollector
from master_agent.server.app import create_app


@pytest.fixture
def make_app(monkeypatch):
    """Builds the app with an explicit agent on a fake client.

    Without an explicit `agent`, `create_app()` resolves the default
    `build_master_agent()`, which without `MASTER_FAKE_CLIENT=true` tries a real
    `OpenAIChatCompletionClient` and fails on a clean clone with no
    credentials. The right pattern is already in conftest.py's fixtures:
    always pass a fake `chat_client`.
    """

    # This endpoint has two sources -- the local buffer and the shared stream --
    # and these tests are about the first. The shared one has its own tests.
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")

    def _make(collector: LogCollector | None = None):
        agent = build_master_agent(chat_client=FakeStreamingChatClient())
        return create_app(agent=agent, collector=collector or LogCollector())

    return _make

def test_logs_endpoint_returns_collected_lines(make_app):
    app = make_app()

    with TestClient(app) as client:
        logging.getLogger("master_agent.tools").info("plan written")
        body = client.get("/logs").json()

    assert [e["message"] for e in body["entries"] if e["source"] == "tools"] == [
        "plan written"
    ]
    assert body["cursor"] != ""
    assert body["dropped"] == 0

def test_logs_endpoint_honours_the_cursor(make_app):
    app = make_app()

    with TestClient(app) as client:
        logging.getLogger("master_agent.tools").info("one")
        first = client.get("/logs").json()
        logging.getLogger("master_agent.tools").info("two")
        second = client.get("/logs", params={"cursor": first["cursor"]}).json()

    assert [e["message"] for e in second["entries"]] == ["two"]

def test_logs_endpoint_never_leaks_library_logs(make_app):
    app = make_app()

    with TestClient(app) as client:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=secret")
        body = client.get("/logs").json()

    assert [e for e in body["entries"] if "httpx" in e["source"]] == []

def test_cors_allows_the_browser_to_read_logs(make_app):

    app = make_app()

    with TestClient(app) as client:
        response = client.options(
            "/logs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

def test_default_state_carries_an_empty_plan():
    from master_agent.server.app import DEFAULT_STATE

    assert DEFAULT_STATE["plan"] == {"status": "idle", "steps": []}
    assert DEFAULT_STATE["artifacts"] == []

def test_shutdown_detaches_the_log_handler(make_app):

    logger = logging.getLogger("master_agent")
    baseline = len(logger.handlers)
    app = make_app()

    with TestClient(app):
        assert len(logger.handlers) == baseline + 1

    assert len(logger.handlers) == baseline


def test_liveness_answers_without_touching_anything(make_app):
    with TestClient(make_app()) as client:
        alive = client.get("/health/live")

    assert alive.status_code == 200
    assert alive.json()["status"] == "alive"


def test_readiness_is_green_without_configured_dependencies(make_app, monkeypatch):
    monkeypatch.setenv("MASTER_MEMORY_SERVICE_URL", "")
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")

    with TestClient(make_app()) as client:
        ready = client.get("/health/ready")

    # What is not configured cannot be down: a laboratory with the agent alone
    # is ready, and says which dependencies it checked.
    assert ready.status_code == 200
    assert ready.json()["checked"] == []


def test_readiness_fails_when_the_memory_service_is_unreachable(make_app, monkeypatch):
    monkeypatch.setenv("MASTER_MEMORY_SERVICE_URL", "http://memory.invalid")
    monkeypatch.setenv("MASTER_POSTGRES_DSN", "")

    with TestClient(make_app()) as client:
        ready = client.get("/health/ready")

    # The conversation lives in the memory service: answering without it means
    # starting every thread from scratch, quietly.
    assert ready.status_code == 503
    assert "memory" in ready.json()["detail"]
