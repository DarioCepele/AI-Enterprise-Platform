import logging

from fastapi.testclient import TestClient

from demo.logging_bridge import LogCollector
from demo.server.app import create_app


def test_logs_endpoint_returns_collected_lines():
    collector = LogCollector()
    app = create_app(collector=collector)

    with TestClient(app) as client:
        logging.getLogger("demo.tools").info("piano scritto")
        body = client.get("/logs").json()

    assert [e["message"] for e in body["entries"]] == ["piano scritto"]
    assert body["cursor"] > 0
    assert body["dropped"] == 0


def test_logs_endpoint_honours_the_cursor():
    collector = LogCollector()
    app = create_app(collector=collector)

    with TestClient(app) as client:
        logging.getLogger("demo.tools").info("uno")
        first = client.get("/logs").json()
        logging.getLogger("demo.tools").info("due")
        second = client.get("/logs", params={"cursor": first["cursor"]}).json()

    assert [e["message"] for e in second["entries"]] == ["due"]


def test_logs_endpoint_never_leaks_library_logs():
    collector = LogCollector()
    app = create_app(collector=collector)

    with TestClient(app) as client:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=segreto")
        body = client.get("/logs").json()

    assert body["entries"] == []


def test_cors_allows_the_browser_to_read_logs():
    # Il preflight di una GET cross-origin fallisce se allow_methods resta
    # solo POST, e fallisce solo nel browser: nessun altro test lo vede.
    app = create_app(collector=LogCollector())

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
