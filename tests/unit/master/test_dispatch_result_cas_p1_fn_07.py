"""P1-FN-07 回归:_record_dispatch_result 消费 update_dispatch_status CAS 返回值。

缺陷 P1-FN-07：
scheduler_loop._record_dispatch_result FAILED 分支之前忽略 update_dispatch_status
的返回值。若 CAS 谓词被并发路径(retry_loop/reconcile/其他 worker)推到
终态而拒绝,本次 FAILED update 会 no-op,但 caller 仍会走 log/WS/retry
流程,导致同一 run 有多次 retry 或迟到 FAILED 覆盖已成功的日志。

修复:CAS 返回 False 时返回 (None, False) 表示"忽略本次结算",不写日志/
WS/触发 retry。上游 caller 见 None 就跳过下一步。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

FENCING_TOKEN = 7


@pytest.mark.asyncio
async def test_failed_result_rejects_legacy_non_atomic_path():
    """失败结果不得绕过 failure settlement 分步写终态和 retry。"""
    from antcode_master.control.scheduler_loop import SchedulerService

    scheduler = object.__new__(SchedulerService)
    scheduler._persist_result_fields = AsyncMock()
    scheduler._log_execution = AsyncMock()

    from antcode_master.control import scheduler_loop as sl_mod

    original = sl_mod.execution_status_service.update_dispatch_status
    sl_mod.execution_status_service.update_dispatch_status = AsyncMock(return_value=False)
    try:
        execution = MagicMock()
        result = {"success": False, "error": "worker gone"}
        with pytest.raises(RuntimeError, match="failure_settlement"):
            await scheduler._record_dispatch_result(execution, "run-1", result)

        sl_mod.execution_status_service.update_dispatch_status.assert_not_awaited()
        scheduler._log_execution.assert_not_awaited()
        scheduler._persist_result_fields.assert_not_awaited()
    finally:
        sl_mod.execution_status_service.update_dispatch_status = original


@pytest.mark.asyncio
async def test_failed_result_uses_atomic_settlement_before_side_effects(monkeypatch):
    """权威入口原子持久化失败与 retry fact，再写任务日志。"""
    from antcode_master.control import scheduler_failure_wiring
    from antcode_master.control.failure_settlement import FailureSettlementResult
    from antcode_master.control.scheduler_loop import SchedulerService

    scheduler = object.__new__(SchedulerService)
    scheduler._log_execution = AsyncMock()
    settle = AsyncMock(return_value=FailureSettlementResult(settled=True))
    deliver = AsyncMock()
    monkeypatch.setattr(scheduler_failure_wiring, "settle_failure", settle)
    monkeypatch.setattr(scheduler_failure_wiring, "deliver_retry_intent", deliver)
    execution = SimpleNamespace(run_id="run-1", scheduler_fencing_token=FENCING_TOKEN)

    outcome = await scheduler_failure_wiring.record_dispatch_outcome(
        scheduler,
        execution=execution,
        result={"success": False, "error": "worker gone"},
    )

    assert outcome == (False, False)
    request = settle.await_args.args[0]
    assert request.run_id == "run-1"
    assert request.authority_token == FENCING_TOKEN
    assert request.expected_scheduler_fencing_token == FENCING_TOKEN
    deliver.assert_awaited_once_with(None)
    assert scheduler._log_execution.await_args.args[1] == "ERROR"


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
