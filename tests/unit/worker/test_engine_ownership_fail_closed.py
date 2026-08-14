from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.engine.engine import MILLISECONDS_PER_SECOND, Engine
from antcode_worker.transport.base import TaskMessage


def _engine(transport=None) -> Engine:
    engine = Engine.__new__(Engine)
    engine._worker_id_cache = None
    engine._transport = transport or SimpleNamespace(
        _worker_id="worker-1",
        _lease_id="lease-7",
        claim_run_ownership=AsyncMock(return_value=True),
        renew_run_ownership=AsyncMock(return_value=True),
        release_run_ownership=AsyncMock(return_value=True),
    )
    return engine


@pytest.mark.asyncio
async def test_ownership_claim_fails_closed_without_transport_client():
    # P1-DR-01: ownership 只允许用 transport 注入的 ACL client；缺失时
    # 显式拒绝，绝不回退全局 get_redis_client()。
    engine = _engine(
        transport=SimpleNamespace(
            _worker_id="worker-1",
            _lease_id="lease-7",
        )
    )

    with pytest.raises(RuntimeError, match="ownership"):
        await engine._claim_run_ownership("run-1")


@pytest.mark.asyncio
async def test_ownership_claim_uses_transport_control_boundary():
    engine = _engine()

    assert await engine._claim_run_ownership("run-1") is True
    engine._transport.claim_run_ownership.assert_awaited_once_with(
        "run-1",
        Engine._RUN_OWNERSHIP_TTL_SECONDS * MILLISECONDS_PER_SECOND,
    )


@pytest.mark.asyncio
async def test_ownership_claim_raises_when_lease_generation_superseded():
    # fence 返回 LEASE_STALE(-1) 时必须抛错终止接手，不能静默当竞争处理。
    engine = _engine()
    engine._transport.claim_run_ownership.side_effect = RuntimeError("lease 已被新代际取代")

    with pytest.raises(RuntimeError, match="新代际"):
        await engine._claim_run_ownership("run-1")


@pytest.mark.asyncio
async def test_ownership_contention_defers_without_ack_or_requeue():
    transport = MagicMock()
    transport.is_connected = True
    transport.defer_task = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock()
    engine = Engine(transport=transport, executor=MagicMock())
    engine._polling = True
    message = TaskMessage(
        task_id="task-1",
        project_id="project-1",
        run_id="run-1",
        receipt="{antcode}:task:ready:worker-1|1-0",
    )

    async def poll_once(*_args, **_kwargs):
        engine._polling = False
        return message

    transport.poll_task = AsyncMock(side_effect=poll_once)
    engine._claim_run_ownership = AsyncMock(return_value=False)

    await engine._poll_loop()

    transport.defer_task.assert_awaited_once_with(
        message.receipt,
        reason="ownership_contention run_id=run-1",
    )
    transport.ack_task.assert_not_awaited()
    assert await engine.state_manager.get("run-1") is None
    assert engine.scheduler.size == 0


@pytest.mark.asyncio
async def test_local_duplicate_does_not_ack_active_message():
    transport = MagicMock()
    transport.is_connected = True
    transport.defer_task = AsyncMock()
    transport.ack_task = AsyncMock()
    engine = Engine(transport=transport, executor=MagicMock())
    engine._polling = True
    await engine.state_manager.add("run-1", "task-1", receipt="original|1-0")
    message = TaskMessage(
        task_id="task-1",
        project_id="project-1",
        run_id="run-1",
        receipt="duplicate|2-0",
    )

    async def poll_once(*_args, **_kwargs):
        engine._polling = False
        return message

    transport.poll_task = AsyncMock(side_effect=poll_once)
    engine._claim_run_ownership = AsyncMock()

    await engine._poll_loop()

    transport.ack_task.assert_not_awaited()
    transport.defer_task.assert_not_awaited()
    engine._claim_run_ownership.assert_not_awaited()
