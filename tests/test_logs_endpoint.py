import logging

import pytest
from fastapi.testclient import TestClient

from demo.agents.master import build_master_agent
from demo.chat_clients.fake import FakeStreamingChatClient
from demo.logging_bridge import LogCollector
from demo.server.app import create_app


@pytest.fixture
def make_app():
    """Costruisce l'app con un agente esplicito su fake client.

    Senza `agent` esplicito, `create_app()` risolve `build_master_agent()` di
    default, che senza `DEMO_FAKE_CLIENT=true` prova un `OpenAIChatCompletionClient`
    reale e fallisce su un clone pulito senza credenziali. Il pattern corretto
    e' gia' nelle fixture di conftest.py: passare sempre un `chat_client` finto.
    """

    def _make(collector: LogCollector | None = None):
        agent = build_master_agent(chat_client=FakeStreamingChatClient())
        return create_app(agent=agent, collector=collector or LogCollector())

    return _make


def test_logs_endpoint_returns_collected_lines(make_app):
    app = make_app()

    with TestClient(app) as client:
        logging.getLogger("demo.tools").info("piano scritto")
        body = client.get("/logs").json()

    assert [e["message"] for e in body["entries"]] == ["piano scritto"]
    assert body["cursor"] > 0
    assert body["dropped"] == 0


def test_logs_endpoint_honours_the_cursor(make_app):
    app = make_app()

    with TestClient(app) as client:
        logging.getLogger("demo.tools").info("uno")
        first = client.get("/logs").json()
        logging.getLogger("demo.tools").info("due")
        second = client.get("/logs", params={"cursor": first["cursor"]}).json()

    assert [e["message"] for e in second["entries"]] == ["due"]


def test_logs_endpoint_never_leaks_library_logs(make_app):
    app = make_app()

    with TestClient(app) as client:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=segreto")
        body = client.get("/logs").json()

    assert body["entries"] == []


def test_cors_allows_the_browser_to_read_logs(make_app):
    # Il preflight di una GET cross-origin fallisce se allow_methods resta
    # solo POST, e fallisce solo nel browser: nessun altro test lo vede.
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
    from demo.server.app import DEFAULT_STATE

    # Senza questo, il pannello del piano non ha una forma da rendere prima
    # che il primo todo_write arrivi, e deve indovinarla.
    assert DEFAULT_STATE["plan"] == {"status": "idle", "steps": []}
    assert DEFAULT_STATE["artifacts"] == []


def test_shutdown_detaches_the_log_handler(make_app):
    # attach() aggancia un handler a logging.getLogger("demo"), che e' un
    # singleton di processo: senza detach() allo shutdown, ogni create_app()
    # ne accumula uno nuovo per la vita del processo.
    logger = logging.getLogger("demo")
    baseline = len(logger.handlers)
    app = make_app()

    with TestClient(app):
        assert len(logger.handlers) == baseline + 1

    assert len(logger.handlers) == baseline
