"""P1-FN-01(a): 取消必须是派发 fence —— dispatch CAS 失败即中止派发并收敛。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_master.control import scheduler_loop


class _FenceStatusService:
    def __init__(self, claim_result: bool):
        self._claim_result = claim_result
        self.dispatch_updates: list[dict] = []

    async def update_dispatch_status(self, **update):
        self.dispatch_updates.append(update)
        return self._claim_result


@pytest.mark.asyncio
async def test_dispatch_aborts_when_cas_lost_to_cancel(monkeypatch):
    """CAS 失败（run 已被取消/推进）时不得继续选 worker、不得投递任务。"""
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    status_service = _FenceStatusService(claim_result=False)
    monkeypatch.setattr(scheduler_loop, "execution_status_service", status_service)

    from antcode_master.dispatch import selector

    resolve = AsyncMock(side_effect=AssertionError("CAS 失败后不得继续选 worker"))
    monkeypatch.setattr(selector.execution_resolver, "resolve_execution_worker", resolve)

    result = await service._dispatch_and_run(
        SimpleNamespace(id=1, name="task"),
        SimpleNamespace(type="code"),
        None,
        SimpleNamespace(run_id="run-cancelled"),
        "run-cancelled",
        datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["aborted"] is True
    resolve.assert_not_awaited()
    # 只发生了一次 DISPATCHING claim 尝试
    assert len(status_service.dispatch_updates) == 1


@pytest.mark.asyncio
async def test_aborted_result_does_not_overwrite_state_or_retry(monkeypatch):
    """aborted 结果收敛：不写 dispatch 状态、不计失败、不触发重试。"""
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    status_service = _FenceStatusService(claim_result=True)
    monkeypatch.setattr(scheduler_loop, "execution_status_service", status_service)

    result_success, distributed_pending = await service._record_dispatch_result(
        SimpleNamespace(run_id="run-cancelled"),
        "run-cancelled",
        {"success": False, "aborted": True, "error": "已被取消"},
    )

    # result_success=None → _run_one_execution 既不计失败也不安排重试
    assert result_success is None
    assert distributed_pending is False
    assert status_service.dispatch_updates == []


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_cas_won(monkeypatch):
    """CAS 命中时照常进入 worker 选择（对照组）。"""
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    service._execute_distributed_task = AsyncMock(return_value={"success": True, "distributed": True, "pending": True})
    status_service = _FenceStatusService(claim_result=True)
    monkeypatch.setattr(scheduler_loop, "execution_status_service", status_service)

    from antcode_master.dispatch import selector

    worker = SimpleNamespace(id=5, name="w", public_id="worker-5")
    resolve = AsyncMock(return_value=(worker, "load_balance"))
    monkeypatch.setattr(selector.execution_resolver, "resolve_execution_worker", resolve)

    result = await service._dispatch_and_run(
        SimpleNamespace(id=1, name="task"),
        SimpleNamespace(type="code"),
        None,
        SimpleNamespace(run_id="run-live"),
        "run-live",
        datetime.now(UTC),
    )

    assert result["success"] is True
    resolve.assert_awaited_once()
