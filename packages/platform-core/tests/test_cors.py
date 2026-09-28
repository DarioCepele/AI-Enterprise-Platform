"""Credentials across origins: allowed for named pages, never for everyone."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from platform_core.cors import cors_options

PAGE = "https://app.example.com"


def service(**options: object) -> TestClient:
    async def hello(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/", hello)])
    app.add_middleware(CORSMiddleware, **cors_options([PAGE], **options))
    return TestClient(app)


def test_by_default_no_credentials_are_accepted():
    response = service().get("/", headers={"Origin": PAGE})

    assert response.headers["access-control-allow-origin"] == PAGE
    assert "access-control-allow-credentials" not in response.headers


def test_credentials_are_accepted_for_a_named_page():
    client = service(credentials=True)

    response = client.get("/", headers={"Origin": PAGE, "Cookie": "session=1"})
    preflight = client.options(
        "/",
        headers={
            "Origin": PAGE,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == PAGE
    assert preflight.headers["access-control-allow-credentials"] == "true"


def test_another_page_gets_nothing_even_with_credentials_on():
    response = service(credentials=True).get(
        "/", headers={"Origin": "https://evil.example", "Cookie": "session=1"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_a_wildcard_with_credentials_is_refused():
    with pytest.raises(ValueError, match="origins named"):
        cors_options(["*"], credentials=True)


def test_a_wildcard_without_credentials_stays_the_operator_s_choice():
    assert cors_options(["*"])["allow_origins"] == ["*"]
