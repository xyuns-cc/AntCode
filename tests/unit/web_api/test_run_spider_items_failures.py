"""Spider item storage failures must remain visible to the API layer."""

import importlib
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1.runs import _read_spider_stream


@pytest.mark.asyncio
async def test_spider_stream_rejects_missing_redis(monkeypatch):
    redis_module = importlib.import_module("antcode_core.infrastructure.redis")
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="Redis client unavailable"):
        await _read_spider_stream("run-1", "0", 100)


@pytest.mark.asyncio
async def test_spider_stream_exposes_redis_read_failure(monkeypatch):
    redis_module = importlib.import_module("antcode_core.infrastructure.redis")
    redis = AsyncMock()
    redis.xrange.side_effect = RuntimeError("redis read failed")
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=redis))

    with pytest.raises(RuntimeError, match="redis read failed"):
        await _read_spider_stream("run-1", "0", 100)
