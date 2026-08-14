import json

import pytest
from antcode_gateway.handlers.heartbeat import HeartbeatData, HeartbeatHandler

EXPECTED_TASK_COUNT = 7


class _Pipeline:
    def __init__(self) -> None:
        self.mapping = None
        self.deleted = None

    def hset(self, key, mapping) -> None:
        del key
        self.mapping = mapping

    def expire(self, key, ttl) -> None:
        del key, ttl

    def hdel(self, key, field) -> None:
        del key
        self.deleted = field

    async def execute(self) -> None:
        return None


class _Redis:
    def __init__(self) -> None:
        self.pipe = _Pipeline()

    def pipeline(self, transaction=False):
        assert transaction is False
        return self.pipe


@pytest.mark.asyncio
async def test_heartbeat_requires_redis_client():
    handler = HeartbeatHandler()
    handler._get_redis_client = _missing_redis_client

    with pytest.raises(RuntimeError, match="Redis"):
        await handler.handle(HeartbeatData(worker_id="worker-1"))


async def _missing_redis_client():
    return None


@pytest.mark.asyncio
async def test_gateway_heartbeat_hash_preserves_extended_metrics() -> None:
    redis = _Redis()
    handler = HeartbeatHandler(redis_client=redis)
    spider_stats = {"request_count": 5, "status_codes": {"200": 5}}

    assert await handler.handle(
        HeartbeatData(
            worker_id="worker-1",
            task_count=EXPECTED_TASK_COUNT,
            project_count=3,
            env_count=2,
            spider_stats=spider_stats,
        )
    )

    assert redis.pipe.mapping["task_count"] == str(EXPECTED_TASK_COUNT)
    assert json.loads(redis.pipe.mapping["spider_stats"]) == spider_stats


@pytest.mark.asyncio
async def test_gateway_heartbeat_removes_stale_spider_stats() -> None:
    redis = _Redis()
    handler = HeartbeatHandler(redis_client=redis)

    assert await handler.handle(HeartbeatData(worker_id="worker-1"))

    assert redis.pipe.deleted == "spider_stats"
