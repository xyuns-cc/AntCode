import pytest
from antcode_core.application.services.scheduler.outbox_service import scheduler_outbox_service
from antcode_core.application.services.scheduler.scheduler_service import SchedulerService
from antcode_core.infrastructure.redis.locks import DistributedLock, FencingTokenManager
from antcode_core.infrastructure.redis.rate_limiter import RedisRateLimiter
from antcode_core.infrastructure.redis.stream_client import StreamClient


class _BrokenStreamRedis:
    async def ping(self):
        return True

    async def xgroup_create(self, *_args, **_kwargs):
        return True

    async def xreadgroup(self, *_args, **_kwargs):
        raise RuntimeError("redis stream read failure")

    async def xclaim(self, *_args, **_kwargs):
        raise RuntimeError("redis stream claim failure")

    async def xautoclaim(self, *_args, **_kwargs):
        raise RuntimeError("redis stream failure")


class _PendingSummaryRedis:
    async def ping(self):
        return True

    async def xpending(self, *_args):
        return {
            "pending": 1,
            "min": b"1-0",
            "max": b"1-0",
            "consumers": [{"name": b"worker-a", "pending": 1}],
        }


class _BrokenLockRedis:
    async def eval(self, *_args):
        raise RuntimeError("redis lock failure")


class _MissingTokenRedis:
    async def get(self, *_args):
        return None


@pytest.mark.asyncio
async def test_xautoclaim_failure_is_exposed():
    client = StreamClient(_BrokenStreamRedis())

    with pytest.raises(RuntimeError, match="redis stream failure"):
        await client.xautoclaim("task-stream", "task-workers")


@pytest.mark.asyncio
async def test_stream_xreadgroup_multi_failure_is_exposed():
    client = StreamClient(_BrokenStreamRedis())

    with pytest.raises(RuntimeError, match="redis stream read failure"):
        await client.xreadgroup_multi(["task-stream"], "task-workers")


@pytest.mark.asyncio
async def test_stream_xclaim_failure_is_exposed():
    client = StreamClient(_BrokenStreamRedis())

    with pytest.raises(RuntimeError, match="redis stream claim failure"):
        await client.xclaim("task-stream", ["1-0"], "task-workers")


@pytest.mark.asyncio
async def test_xpending_accepts_current_redis_dict_shape():
    summary = await StreamClient(_PendingSummaryRedis()).xpending("task-stream", "task-workers")

    assert summary == {
        "pending_count": 1,
        "min_id": "1-0",
        "max_id": "1-0",
        "consumers": {"worker-a": 1},
    }


@pytest.mark.asyncio
async def test_distributed_lock_release_failure_is_exposed():
    lock = DistributedLock("test")
    lock._token = "token"
    lock._redis = _BrokenLockRedis()

    with pytest.raises(RuntimeError, match="redis lock failure"):
        await lock.release()


@pytest.mark.asyncio
async def test_distributed_lock_extend_failure_is_exposed():
    lock = DistributedLock("test")
    lock._token = "token"
    lock._redis = _BrokenLockRedis()

    with pytest.raises(RuntimeError, match="redis lock failure"):
        await lock.extend()


@pytest.mark.asyncio
async def test_distributed_lock_renew_failure_clears_local_ownership():
    class RenewFailureLock(DistributedLock):
        async def extend(self, additional_seconds=None):
            return False

    lock = RenewFailureLock("test", renew_interval=0.001)
    lock._token = "token"

    with pytest.raises(RuntimeError, match="锁续期失败"):
        await lock._renew_loop()

    assert lock.is_locked is False


@pytest.mark.asyncio
async def test_fencing_token_missing_key_is_not_fail_open():
    manager = FencingTokenManager()
    manager._redis = _MissingTokenRedis()

    assert await manager.validate_token_gte(1) is False


@pytest.mark.asyncio
async def test_scheduler_event_publish_failure_is_exposed(monkeypatch):
    async def fail_enqueue(**_kwargs):
        raise RuntimeError("outbox failed")

    service = SchedulerService()
    monkeypatch.setattr(scheduler_outbox_service, "enqueue", fail_enqueue)

    with pytest.raises(RuntimeError, match="outbox failed"):
        await service._publish_event("task_changed", 1)


@pytest.mark.asyncio
async def test_rate_limiter_redis_failure_is_exposed(monkeypatch):
    async def broken_client():
        raise RuntimeError("rate limiter redis failed")

    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.client.get_redis_client",
        broken_client,
    )

    limiter = RedisRateLimiter()

    with pytest.raises(RuntimeError, match="rate limiter redis failed"):
        await limiter.is_allowed("client-1", limit=10, period=60)
