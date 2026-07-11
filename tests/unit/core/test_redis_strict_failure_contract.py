import pytest
from antcode_core.application.services.scheduler.outbox_service import scheduler_outbox_service
from antcode_core.application.services.scheduler.redis_queue import RedisQueueBackend
from antcode_core.application.services.scheduler.scheduler_service import SchedulerService
from antcode_core.infrastructure.redis.bloom_client import BloomFilterClient
from antcode_core.infrastructure.redis.locks import DistributedLock, FencingTokenManager
from antcode_core.infrastructure.redis.rate_limiter import RedisRateLimiter
from antcode_core.infrastructure.redis.stream_client import StreamClient as LegacyStreamClient
from antcode_core.infrastructure.redis.streams import StreamClient


class _BloomUnavailableRedis:
    set_calls = 0

    async def execute_command(self, *_args):
        raise RuntimeError("unknown command 'BF.INFO'")

    async def sadd(self, *_args):
        self.set_calls += 1
        return 1


class _BloomAddFailureRedis:
    set_calls = 0

    async def execute_command(self, command, *_args):
        if command == "BF.INFO":
            raise RuntimeError("not found")
        raise RuntimeError("bf.add failed")

    async def sadd(self, *_args):
        self.set_calls += 1
        return 1


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


class _BrokenLockRedis:
    async def eval(self, *_args):
        raise RuntimeError("redis lock failure")


class _MissingTokenRedis:
    async def get(self, *_args):
        return None


@pytest.mark.asyncio
async def test_bloom_client_requires_redisbloom_module():
    redis = _BloomUnavailableRedis()
    client = BloomFilterClient(redis)

    with pytest.raises(RuntimeError, match="RedisBloom"):
        await client.bf_add("dedup", "item-1")

    assert redis.set_calls == 0


@pytest.mark.asyncio
async def test_bloom_operation_failure_is_not_downgraded_to_set():
    redis = _BloomAddFailureRedis()
    client = BloomFilterClient(redis)

    with pytest.raises(RuntimeError, match="bf.add failed"):
        await client.bf_add("dedup", "item-1")

    assert redis.set_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [StreamClient, LegacyStreamClient])
async def test_xautoclaim_failure_is_exposed(client_type):
    client = client_type(_BrokenStreamRedis())

    with pytest.raises(RuntimeError, match="redis stream failure"):
        await client.xautoclaim("task-stream")


@pytest.mark.asyncio
async def test_legacy_stream_xreadgroup_multi_failure_is_exposed():
    client = LegacyStreamClient(_BrokenStreamRedis())

    with pytest.raises(RuntimeError, match="redis stream read failure"):
        await client.xreadgroup_multi(["task-stream"])


@pytest.mark.asyncio
async def test_legacy_stream_xclaim_failure_is_exposed():
    client = LegacyStreamClient(_BrokenStreamRedis())

    with pytest.raises(RuntimeError, match="redis stream claim failure"):
        await client.xclaim("task-stream", ["1-0"])


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
    monkeypatch.setattr(service, "_control_plane", lambda: True)
    monkeypatch.setattr(scheduler_outbox_service, "enqueue", fail_enqueue)

    with pytest.raises(RuntimeError, match="outbox failed"):
        await service._publish_event("task_changed", 1)


def test_redis_queue_contains_failure_is_exposed(monkeypatch):
    async def fail_contains(_task_id):
        raise RuntimeError("contains failed")

    queue = RedisQueueBackend("redis://127.0.0.1:6379/0")
    monkeypatch.setattr(queue, "_contains_async", fail_contains)

    with pytest.raises(RuntimeError, match="contains failed"):
        queue.contains("task-1")


def test_redis_queue_size_failure_is_exposed(monkeypatch):
    async def fail_size():
        raise RuntimeError("size failed")

    queue = RedisQueueBackend("redis://127.0.0.1:6379/0")
    monkeypatch.setattr(queue, "_size_async", fail_size)

    with pytest.raises(RuntimeError, match="size failed"):
        queue.size()


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
