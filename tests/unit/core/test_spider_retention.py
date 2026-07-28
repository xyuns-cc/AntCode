"""SpiderData retention 共享配置与 legacy Redis sink 测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.spider_retention import SpiderRetention
from antcode_scrapy.sinks.redis_sink import RedisSpiderDataSink


def _fake_redis() -> MagicMock:
    redis = MagicMock()
    redis.zadd = AsyncMock(return_value=1)
    redis.zremrangebyscore = AsyncMock(return_value=0)
    redis.eval = AsyncMock(return_value=1)
    redis.zrem = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.persist = AsyncMock(return_value=True)
    redis.aclose = AsyncMock()
    return redis


def test_retention_defaults_to_unlimited(monkeypatch) -> None:
    monkeypatch.delenv("MAX_LEN", raising=False)
    monkeypatch.delenv("TTL", raising=False)

    retention = SpiderRetention.from_env(stream_max_len_env="MAX_LEN", ttl_seconds_env="TTL")

    assert retention == SpiderRetention(stream_max_len=0, ttl_seconds=0)


@pytest.mark.parametrize("value", ["", "   ", "-1", "1.5", "invalid"])
def test_invalid_retention_env_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("MAX_LEN", value)

    with pytest.raises(ValueError, match="非负整数"):
        SpiderRetention.from_env(stream_max_len_env="MAX_LEN", ttl_seconds_env="TTL")


@pytest.mark.asyncio
async def test_legacy_redis_sink_is_explicitly_disabled() -> None:
    sink = RedisSpiderDataSink("redis://localhost")

    with pytest.raises(RuntimeError, match="直写模式已停用"):
        await sink.open(run_id="run-1", project_id="project-1", spider_name="rule", namespace="antcode")
