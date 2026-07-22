"""P1-GW-05 回归:_report_result 无论成功失败都释放 run ownership。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-GW-05:
_report_result 只在成功路径调 _release_run_ownership;失败路径 raise
RuntimeError 但不释放,ownership 键留在 Redis 至 TTL(≈65 分钟)才自动
过期。这段时间 L2 通过 XAUTOCLAIM 拿到 PEL 后 claim_run_ownership 会
HELD_BY_OTHER,dead-holder takeover 要求 L1 Lease 已死(活着的 L1
retry 期内 Lease 还活),PEL 反复空转,任务永远不会重跑成功。

修复后:try/finally 保证任何路径都主动 release。release 自身失败只
warn,不覆盖原异常。

本测试锁死:
1. 上报成功 → release 被调
2. 上报失败(retry 耗尽 raise) → release 仍被调,原异常保留
3. release 失败 → 只 warn,不覆盖上报层的异常/成功状态
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecResult, RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.base import TransportMode


def _make_engine():
    transport = MagicMock()
    transport.mode = TransportMode.DIRECT
    transport.worker_id = "w-1"
    transport._worker_id = "w-1"
    transport._lease_id = "lease-1"
    executor = MagicMock()
    engine = Engine(transport=transport, executor=executor, max_concurrent=1)
    engine._resolve_lease_id = MagicMock(return_value="lease-1")
    engine._resolve_worker_id = MagicMock(return_value="w-1")
    return engine, transport


def _make_context_and_result(status: RunStatus = RunStatus.SUCCESS):
    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="proj-1",
        receipt="pel-receipt-1",
    )
    result = ExecResult(
        run_id="run-1",
        status=status,
        exit_code=0,
        exit_reason=ExitReason.NORMAL,
        started_at=datetime(2026, 7, 22, 12, 0, 0),
        finished_at=datetime(2026, 7, 22, 12, 0, 1),
        duration_ms=1000.0,
    )
    return context, result


@pytest.mark.asyncio
async def test_release_called_on_success():
    """成功路径:release 被调一次。"""
    engine, transport = _make_engine()
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=True)
    release = AsyncMock()
    engine._release_run_ownership = release  # type: ignore[method-assign]

    context, result = _make_context_and_result()
    await engine._report_result(context, result)

    release.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_release_called_on_report_failure():
    """P1-GW-05 关键:report 失败 → 抛出 RuntimeError,但 release 已被调。"""
    engine, transport = _make_engine()
    transport.report_result = AsyncMock(return_value=False)  # 5 次重试全 False
    transport.ack_task = AsyncMock(return_value=True)
    release = AsyncMock()
    engine._release_run_ownership = release  # type: ignore[method-assign]

    # 缩短重试等待,避免测试跑 30s
    engine._SETTLE_MAX_ATTEMPTS = 1  # type: ignore[assignment]
    engine._SETTLE_BACKOFF_BASE_SECONDS = 0  # type: ignore[assignment]

    context, result = _make_context_and_result()
    with pytest.raises(RuntimeError, match="结果上报失败"):
        await engine._report_result(context, result)

    release.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_release_called_on_ack_failure():
    """P1-GW-05 关键:report 成功但 ACK 失败 → raise,release 仍被调。"""
    engine, transport = _make_engine()
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=False)
    release = AsyncMock()
    engine._release_run_ownership = release  # type: ignore[method-assign]

    engine._SETTLE_MAX_ATTEMPTS = 1  # type: ignore[assignment]
    engine._SETTLE_BACKOFF_BASE_SECONDS = 0  # type: ignore[assignment]

    context, result = _make_context_and_result()
    with pytest.raises(RuntimeError, match="任务 ACK 失败"):
        await engine._report_result(context, result)

    release.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_release_exception_does_not_mask_report_exception():
    """P1-GW-05:release 抛异常不能覆盖 report 层的原异常。"""
    engine, transport = _make_engine()
    transport.report_result = AsyncMock(return_value=False)
    release = AsyncMock(side_effect=RuntimeError("Redis 失联"))
    engine._release_run_ownership = release  # type: ignore[method-assign]

    engine._SETTLE_MAX_ATTEMPTS = 1  # type: ignore[assignment]
    engine._SETTLE_BACKOFF_BASE_SECONDS = 0  # type: ignore[assignment]

    context, result = _make_context_and_result()
    # 应传出 "结果上报失败"(原异常),而不是 "Redis 失联"
    with pytest.raises(RuntimeError, match="结果上报失败"):
        await engine._report_result(context, result)

    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_exception_does_not_mask_success():
    """P1-GW-05:release 失败但上报本身成功时,应记录 warn 而不重新 raise。"""
    engine, transport = _make_engine()
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=True)
    release = AsyncMock(side_effect=RuntimeError("Redis 失联"))
    engine._release_run_ownership = release  # type: ignore[method-assign]

    context, result = _make_context_and_result()
    # 应正常返回(不 raise),release warn 已入日志
    await engine._report_result(context, result)

    release.assert_awaited_once()
