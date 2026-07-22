"""P1-FN-07 回归:_record_dispatch_result 消费 update_dispatch_status CAS 返回值。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-FN-07:
scheduler_loop._record_dispatch_result FAILED 分支之前忽略 update_dispatch_status
的返回值。若 CAS 谓词被并发路径(retry_loop/reconcile/其他 worker)推到
终态而拒绝,本次 FAILED update 会 no-op,但 caller 仍会走 log/WS/retry
流程,导致同一 run 有多次 retry 或迟到 FAILED 覆盖已成功的日志。

修复:CAS 返回 False 时返回 (None, False) 表示"忽略本次结算",不写日志/
WS/触发 retry。上游 caller 见 None 就跳过下一步。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_failed_result_with_cas_conflict_returns_none_and_skips_side_effects():
    """P1-FN-07 关键:CAS 拒绝时不写 log/不推 WS/不触发 retry。"""
    from antcode_master.control.scheduler_loop import SchedulerService

    scheduler = object.__new__(SchedulerService)
    scheduler._persist_result_fields = AsyncMock()
    scheduler._log_execution = AsyncMock()
    scheduler._push_execution_status = AsyncMock()

    # 模拟 update_dispatch_status 返回 False (并发已推终态)
    from antcode_master.control import scheduler_loop as sl_mod

    original = sl_mod.execution_status_service.update_dispatch_status
    sl_mod.execution_status_service.update_dispatch_status = AsyncMock(return_value=False)
    try:
        execution = MagicMock()
        result = {"success": False, "error": "worker gone"}
        outcome = await scheduler._record_dispatch_result(execution, "run-1", result)

        # 返回 (None, False) 表示忽略本次结算
        assert outcome == (None, False)
        # 没有 ERROR 日志,只有 DEBUG(表明被跳过)
        assert scheduler._log_execution.await_count == 1
        assert scheduler._log_execution.await_args.args[1] == "DEBUG"
        # 没推 WS
        scheduler._push_execution_status.assert_not_awaited()
    finally:
        sl_mod.execution_status_service.update_dispatch_status = original


@pytest.mark.asyncio
async def test_failed_result_with_cas_success_still_writes_log_and_ws():
    """P1-FN-07:CAS 成功时(True)仍按原路径 log + WS + 返回 (False, False)。"""
    from antcode_master.control.scheduler_loop import SchedulerService

    scheduler = object.__new__(SchedulerService)
    scheduler._persist_result_fields = AsyncMock()
    scheduler._log_execution = AsyncMock()
    scheduler._push_execution_status = AsyncMock()

    from antcode_master.control import scheduler_loop as sl_mod

    original = sl_mod.execution_status_service.update_dispatch_status
    sl_mod.execution_status_service.update_dispatch_status = AsyncMock(return_value=True)
    try:
        execution = MagicMock()
        result = {"success": False, "error": "worker gone"}
        outcome = await scheduler._record_dispatch_result(execution, "run-1", result)

        # 常规失败路径:返回 (False, False)
        assert outcome == (False, False)
        # ERROR 日志 + WS 均写入
        assert scheduler._log_execution.await_count == 1
        assert scheduler._log_execution.await_args.args[1] == "ERROR"
        scheduler._push_execution_status.assert_awaited_once()
    finally:
        sl_mod.execution_status_service.update_dispatch_status = original


@pytest.mark.asyncio
async def test_aborted_result_stays_at_no_op():
    """P1-FN-07 反面:aborted(前置 CAS 失败)保持原有 (None, False) 语义。"""
    from antcode_master.control.scheduler_loop import SchedulerService

    scheduler = object.__new__(SchedulerService)
    scheduler._log_execution = AsyncMock()

    execution = MagicMock()
    result = {"success": False, "aborted": True, "error": "already cancelled"}
    outcome = await scheduler._record_dispatch_result(execution, "run-1", result)

    assert outcome == (None, False)
    # WARNING 日志
    assert scheduler._log_execution.await_count == 1
    assert scheduler._log_execution.await_args.args[1] == "WARNING"
