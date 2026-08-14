"""RedisTransport shutdown lifecycle tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from antcode_core.infrastructure.redis.factory import create_async_redis_client
from antcode_worker.transport.base import WorkerState
from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer, ReclaimConfig, ReclaimedTask
from antcode_worker.transport.redis.transport import RedisTransport
from redis.exceptions import ConnectionError as RedisConnectionError


class _CloseAwareRedis:
    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.closed.set()


class _SocketBlockedReclaimer:
    def __init__(self, redis: _CloseAwareRedis) -> None:
        self._redis = redis
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1
        await self._redis.closed.wait()


class _CancellationSwallowingRedis:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def xpending_range(self, *args, **kwargs):
        self.entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return []


def test_standalone_factory_client_owns_its_connection_pool():
    client = create_async_redis_client("redis://localhost:6379/0")

    assert client.auto_close_connection_pool is True


@pytest.mark.asyncio
async def test_reclaimer_stops_when_redis_swallows_cancellation():
    redis = _CancellationSwallowingRedis()
    reclaimer = PendingTaskReclaimer(
        redis_client=redis,
        worker_id="worker-1",
        config=ReclaimConfig(check_interval_seconds=30),
    )
    await reclaimer.start()
    await redis.entered.wait()

    await asyncio.wait_for(reclaimer.stop(), timeout=0.5)

    assert reclaimer.is_running is False


@pytest.mark.asyncio
async def test_reclaimer_uses_configured_consumer_group_for_recovery_and_queries():
    redis = AsyncMock()
    redis.xpending_range.return_value = []
    redis.xpending.return_value = {"pending": 0}
    redis.eval.side_effect = [[1, "2-0"], 1]
    reclaimer = PendingTaskReclaimer(
        redis_client=redis,
        worker_id="worker-1",
        consumer_group="custom-workers",
    )

    assert await reclaimer.reclaim_once() == []
    assert await reclaimer.get_pending_count() == 0
    await reclaimer._move_to_dead_letter(
        "{antcode}:task:ready:worker-1",
        ReclaimedTask(
            message_id="1-0",
            data={"task_id": "task-1"},
            idle_time_ms=3_900_000,
            delivery_count=4,
        ),
    )

    redis.xpending_range.assert_awaited_once_with(
        "{antcode}:task:ready:worker-1",
        "custom-workers",
        min="-",
        max="+",
        count=10,
    )
    redis.xpending.assert_awaited_once_with("{antcode}:task:ready:worker-1", "custom-workers")
    assert redis.eval.await_args_list[1].args[3] == "custom-workers"
    assert redis.eval.await_count == 2


@pytest.mark.asyncio
async def test_stop_closes_redis_while_stopping_socket_blocked_reclaimer():
    redis = _CloseAwareRedis()
    reclaimer = _SocketBlockedReclaimer(redis)
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
    )
    transport._redis = redis
    transport._reclaimer = reclaimer
    transport._running = True

    await asyncio.wait_for(transport.stop(grace_period=1.0), timeout=0.5)

    assert redis.close_calls == 1
    assert reclaimer.stop_calls == 1
    assert transport._redis is None
    assert transport._reclaimer is None
    assert transport.state is WorkerState.OFFLINE


@pytest.mark.asyncio
async def test_explicit_stop_wins_against_concurrent_reconnect(monkeypatch):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    transport._redis = _CloseAwareRedis()
    reconnect_stopping = asyncio.Event()
    allow_reconnect = asyncio.Event()
    stop_modes = []

    async def stop_locked(*, close_control):
        stop_modes.append(close_control)
        transport._running = False
        transport._redis = None
        if not close_control:
            reconnect_stopping.set()
            await allow_reconnect.wait()

    async def start_locked():
        transport._running = True
        transport._redis = _CloseAwareRedis()
        return True

    monkeypatch.setattr(transport, "_stop_locked", stop_locked)
    monkeypatch.setattr(transport, "_start_locked", start_locked)

    reconnect_task = asyncio.create_task(transport.reconnect())
    await reconnect_stopping.wait()
    stop_task = asyncio.create_task(transport.stop())
    allow_reconnect.set()
    assert await reconnect_task is True
    await stop_task

    assert stop_modes == [False, True]
    assert transport._running is False
    assert transport._redis is None


@pytest.mark.asyncio
async def test_reconnect_does_not_restart_after_stop_acquires_lifecycle_lock(monkeypatch):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    transport._redis = _CloseAwareRedis()
    stop_entered = asyncio.Event()
    allow_stop = asyncio.Event()

    async def stop_locked(*, close_control):
        assert close_control is True
        stop_entered.set()
        await allow_stop.wait()
        transport._running = False
        transport._redis = None

    start_locked = AsyncMock(return_value=True)
    monkeypatch.setattr(transport, "_stop_locked", stop_locked)
    monkeypatch.setattr(transport, "_start_locked", start_locked)

    stop_task = asyncio.create_task(transport.stop())
    await stop_entered.wait()
    reconnect_task = asyncio.create_task(transport.reconnect())
    allow_stop.set()
    await stop_task

    assert await reconnect_task is False
    start_locked.assert_not_awaited()
    assert transport._running is False
    assert transport._redis is None


@pytest.mark.asyncio
async def test_failed_start_cleans_all_started_resources(monkeypatch):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    redis = AsyncMock()
    reclaimer = AsyncMock()
    deferred_recovery = AsyncMock()
    transport._deferred_recovery = deferred_recovery

    class _Reclaimer:
        def __new__(cls, **_kwargs):
            return reclaimer

    from antcode_core.infrastructure.redis import factory as redis_factory
    from antcode_worker.transport.redis import transport as redis_transport_module

    monkeypatch.setattr(redis_factory, "create_async_redis_client", lambda *args, **kwargs: redis)
    monkeypatch.setattr(redis_transport_module, "ensure_consumer_group", AsyncMock())
    monkeypatch.setattr(redis_transport_module, "PendingTaskReclaimer", _Reclaimer)
    monkeypatch.setattr(transport, "_set_state", AsyncMock(side_effect=RuntimeError("state failed")))

    assert await transport.start() is False

    reclaimer.start.assert_awaited_once()
    reclaimer.stop.assert_awaited_once()
    deferred_recovery.start.assert_awaited_once()
    deferred_recovery.stop.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    assert transport._running is False
    assert transport._redis is None
    assert transport._reclaimer is None


class _HeartbeatPipeline:
    def __init__(self, error=None) -> None:
        self.error = error
        self.execute_calls = 0

    def hset(self, *args, **kwargs):
        return self

    def hdel(self, *args, **kwargs):
        return self

    def expire(self, *args, **kwargs):
        return self

    async def execute(self):
        self.execute_calls += 1
        if self.error:
            raise self.error
        return [1, 1]


class _HeartbeatRedis:
    def __init__(self, pipeline) -> None:
        self._pipeline = pipeline

    def pipeline(self, **kwargs):
        return self._pipeline


@pytest.mark.asyncio
async def test_heartbeat_retry_uses_client_created_by_reconnect(monkeypatch):
    old_pipeline = _HeartbeatPipeline(RedisConnectionError("connection closed"))
    new_pipeline = _HeartbeatPipeline()
    old_redis = _HeartbeatRedis(old_pipeline)
    new_redis = _HeartbeatRedis(new_pipeline)
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._redis = old_redis

    async def reconnect():
        transport._redis = new_redis
        return True

    monkeypatch.setattr(transport, "reconnect", reconnect)
    heartbeat = type("Heartbeat", (), {"status": "online"})()

    await transport._write_legacy_heartbeat_hash(heartbeat, "worker-1")

    assert old_pipeline.execute_calls == 1
    assert new_pipeline.execute_calls == 1
