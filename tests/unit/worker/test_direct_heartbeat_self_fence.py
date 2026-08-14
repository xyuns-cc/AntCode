"""Direct heartbeat generation-loss regression tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from antcode_worker.heartbeat.reporter import HeartbeatReporter
from antcode_worker.transport.base import GenerationLostError
from antcode_worker.transport.redis.transport import RedisTransport


@pytest.mark.asyncio
async def test_direct_heartbeat_412_marks_generation_lost_and_calls_engine_fence() -> None:
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._lease_store = object()
    transport._lease_id = "lease-1"
    transport._direct_control = AsyncMock()
    transport._direct_control.lease_renew.side_effect = GenerationLostError("HTTP 412")
    fence = AsyncMock()
    transport.set_lease_revoked_callback(fence)

    with pytest.raises(GenerationLostError, match="HTTP 412"):
        await transport.lease_renew("lease-1", metrics={})

    assert transport._generation_lost is True
    fence.assert_awaited_once_with("HTTP 412")


@pytest.mark.asyncio
async def test_heartbeat_loop_does_not_swallow_generation_loss() -> None:
    transport = AsyncMock()
    transport.is_connected = True
    transport.send_heartbeat.side_effect = GenerationLostError("HTTP 412")
    reporter = HeartbeatReporter(transport=transport, worker_id="worker-1")
    reporter._running = True

    with pytest.raises(GenerationLostError, match="HTTP 412"):
        await reporter._loop()


@pytest.mark.asyncio
async def test_heartbeat_stop_exposes_completed_task_failure() -> None:
    reporter = HeartbeatReporter(transport=AsyncMock(), worker_id="worker-1")

    async def fail() -> None:
        raise GenerationLostError("HTTP 412")

    reporter._task = asyncio.create_task(fail())
    await asyncio.sleep(0)

    with pytest.raises(ExceptionGroup, match="心跳上报停止失败"):
        await reporter.stop()

    assert reporter._task is None
