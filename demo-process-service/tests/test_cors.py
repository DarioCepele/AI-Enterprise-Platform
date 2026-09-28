"""The Instances panel reads this service from another origin: who may, how.

Cookies go along only when the deployment says so -- a single sign-on proxy
in front needs them -- and never for every origin at once.
"""

from __future__ import annotations

import httpx
import pytest

from process_service.api import create_app
from process_service.catalog import Catalog
from process_service.config import Settings

PAGE = "https://app.example.com"
PREFLIGHT = {
    "Origin": PAGE,
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


async def preflight(settings: Settings) -> httpx.Response:
    app = create_app(catalog=Catalog([]), settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.options("/instances", headers=PREFLIGHT)


async def test_the_page_s_cookies_are_accepted_only_when_configured():
    closed = await preflight(Settings(allowed_origins=PAGE))
    opened = await preflight(Settings(allowed_origins=PAGE, cors_credentials=True))

    assert closed.headers["access-control-allow-origin"] == PAGE
    assert "access-control-allow-credentials" not in closed.headers
    assert opened.headers["access-control-allow-credentials"] == "true"
    assert opened.headers["access-control-allow-origin"] == PAGE


def test_cookies_from_any_origin_are_refused_at_startup():
    with pytest.raises(ValueError, match="origins named"):
        create_app(
            catalog=Catalog([]),
            settings=Settings(allowed_origins="*", cors_credentials=True),
        )
