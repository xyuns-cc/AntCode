import httpx
import pytest
from antcode_web_api.middleware.middleware import BodySizeMiddleware
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def _echo_size(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"size": len(body)})


def _app(limit: int = 5) -> Starlette:
    app = Starlette(routes=[Route("/echo", _echo_size, methods=["POST"])])
    app.add_middleware(BodySizeMiddleware, max_body_size=limit)
    return app


@pytest.mark.asyncio
async def test_rejects_chunked_body_without_content_length():
    async def chunks():
        yield b"123"
        yield b"456"

    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", content=chunks())

    assert response.status_code == 413
    assert response.json()["data"] == {"limit": 5, "received": 6}


@pytest.mark.asyncio
async def test_accepts_body_within_actual_byte_limit():
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", content=b"12345")

    assert response.status_code == 200
    assert response.json() == {"size": 5}
