"""RedisTransport shutdown lifecycle tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from antcode_core.infrastructure.redis.factory import create_async_redis_client
from antcode_worker.transport.base import WorkerState
from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer, ReclaimConfig, ReclaimedTask
from antcode_worker.transport.redis.transport import RedisTransport


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
        "antcode:task:ready:worker-1",
        ReclaimedTask(
            message_id="1-0",
            data={"task_id": "task-1"},
            idle_time_ms=3_900_000,
            delivery_count=4,
        ),
    )

    redis.xpending_range.assert_awaited_once_with(
        "antcode:task:ready:worker-1",
        "custom-workers",
        min="-",
        max="+",
        count=10,
    )
    redis.xpending.assert_awaited_once_with("antcode:task:ready:worker-1", "custom-workers")
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
