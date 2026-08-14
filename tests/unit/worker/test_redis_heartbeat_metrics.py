"""Direct Redis heartbeat metric contract tests."""

import json
from types import SimpleNamespace

import pytest
from antcode_worker.transport.redis.heartbeat_hash import (
    OPTIONAL_HASH_FIELDS,
    _heartbeat_mapping,
    write_legacy_heartbeat_hash,
)
from antcode_worker.transport.redis.transport import RedisTransport

EXPECTED_TASK_COUNT = 12
EXPECTED_PROJECT_COUNT = 3
EXPECTED_ENV_COUNT = 4
EXPECTED_SPIDER_REQUESTS = 25
EXPECTED_MEMORY_BYTES = 1_048_576
EXPECTED_QUEUED_TASKS = 6
EXPECTED_CPU_CORES = 8


def _heartbeat(*, spider_stats=True):
    spider = None
    if spider_stats:
        spider = SimpleNamespace(
            request_count=EXPECTED_SPIDER_REQUESTS,
            response_count=20,
            item_scraped_count=8,
            error_count=1,
            avg_latency_ms=12.5,
            requests_per_minute=7.5,
            status_codes={"200": 20},
            domain_stats=[{"domain": "example.com"}],
        )
    return SimpleNamespace(
        status="online",
        metrics=SimpleNamespace(
            cpu=1.5,
            memory=2.5,
            disk=3.5,
            running_tasks=2,
            max_concurrent_tasks=5,
            task_count=EXPECTED_TASK_COUNT,
            project_count=EXPECTED_PROJECT_COUNT,
            env_count=EXPECTED_ENV_COUNT,
            queued_tasks=EXPECTED_QUEUED_TASKS,
            cpu_cores=EXPECTED_CPU_CORES,
            memory_total_bytes=EXPECTED_MEMORY_BYTES,
            spider_stats=spider,
        ),
    )


def test_direct_heartbeat_preserves_extended_metrics_in_lease_and_hash() -> None:
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    heartbeat = _heartbeat()

    lease_metrics = transport._heartbeat_to_metrics_dict(heartbeat)
    hash_metrics = _heartbeat_mapping(heartbeat)
    spider_stats = json.loads(hash_metrics["spider_stats"])

    assert lease_metrics["task_count"] == EXPECTED_TASK_COUNT
    assert lease_metrics["project_count"] == EXPECTED_PROJECT_COUNT
    assert lease_metrics["env_count"] == EXPECTED_ENV_COUNT
    assert lease_metrics["memory_total_bytes"] == EXPECTED_MEMORY_BYTES
    assert lease_metrics["spider_stats"]["request_count"] == EXPECTED_SPIDER_REQUESTS
    assert hash_metrics["task_count"] == str(EXPECTED_TASK_COUNT)
    assert hash_metrics["project_count"] == str(EXPECTED_PROJECT_COUNT)
    assert hash_metrics["env_count"] == str(EXPECTED_ENV_COUNT)
    assert hash_metrics["memory_total_bytes"] == str(EXPECTED_MEMORY_BYTES)
    assert spider_stats["request_count"] == EXPECTED_SPIDER_REQUESTS


class _Pipeline:
    def __init__(self) -> None:
        self.deleted: tuple[str, ...] = ()

    def hset(self, *_args, **_kwargs):
        return self

    def hdel(self, _key, *fields):
        self.deleted = fields
        return self

    def expire(self, *_args, **_kwargs):
        return self

    async def execute(self):
        return []


class _Redis:
    def __init__(self, pipeline: _Pipeline) -> None:
        self._pipeline = pipeline

    def pipeline(self, **_kwargs):
        return self._pipeline


@pytest.mark.asyncio
async def test_restart_clears_stale_optional_heartbeat_fields() -> None:
    pipeline = _Pipeline()
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._redis = _Redis(pipeline)

    await write_legacy_heartbeat_hash(transport, _heartbeat(spider_stats=False), "worker-1")

    assert set(pipeline.deleted) == OPTIONAL_HASH_FIELDS
