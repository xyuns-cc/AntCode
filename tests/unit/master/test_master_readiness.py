from unittest.mock import AsyncMock

import antcode_master.readiness as readiness_module
import pytest
from antcode_master.readiness import MasterReadinessServer


@pytest.mark.asyncio
async def test_dependencies_ready_checks_redis_and_postgres(monkeypatch):
    redis = AsyncMock()
    database = AsyncMock()
    server = MasterReadinessServer()
    server._ready = True
    monkeypatch.setattr(readiness_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        readiness_module.Tortoise,
        "get_connection",
        lambda _name: database,
    )

    assert await server._dependencies_ready() is True
    redis.ping.assert_awaited_once()
    database.execute_query.assert_awaited_once_with("SELECT 1")


@pytest.mark.asyncio
async def test_dependencies_ready_fails_closed(monkeypatch):
    server = MasterReadinessServer()
    server._ready = True
    monkeypatch.setattr(
        readiness_module,
        "get_redis_client",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )

    assert await server._dependencies_ready() is False


def test_readiness_http_response_status():
    assert MasterReadinessServer._response(True).startswith(b"HTTP/1.1 200 OK")
    assert MasterReadinessServer._response(False).startswith(b"HTTP/1.1 503 Service Unavailable")
