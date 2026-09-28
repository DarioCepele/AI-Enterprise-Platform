"""The body ceiling holds for declared sizes and for streamed ones."""

from __future__ import annotations

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from platform_core.http import BodySizeLimit


async def echo_size(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"size": len(body)})


def build_app() -> BodySizeLimit:
    app = Starlette(routes=[Route("/{path:path}", echo_size, methods=["POST"])])
    return BodySizeLimit(app, default=100, by_prefix={"/uploads": 1000})


async def post(path: str, content: object, headers: dict[str, str] | None = None):
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(path, content=content, headers=headers or {})


async def test_a_small_body_passes():
    response = await post("/agui", b"x" * 50)
    assert response.status_code == 200
    assert response.json() == {"size": 50}


async def test_a_declared_size_over_the_limit_is_refused_before_reading():
    response = await post("/agui", b"x" * 150)
    assert response.status_code == 413


async def test_a_malformed_length_is_a_client_error_not_a_crash():
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        request = client.build_request("POST", "/agui", content=b"x")
        request.headers["content-length"] = "twelve"
        response = await client.send(request)
    assert response.status_code == 400


async def test_a_streamed_body_without_a_length_is_counted():
    async def chunks():
        for _ in range(10):
            yield b"x" * 30

    response = await post("/agui", chunks())
    assert response.status_code == 413


async def test_a_path_can_have_its_own_ceiling():
    response = await post("/uploads", b"x" * 500)
    assert response.status_code == 200
    assert response.json() == {"size": 500}
