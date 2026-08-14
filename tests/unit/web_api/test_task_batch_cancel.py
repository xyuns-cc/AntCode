"""批量任务取消：已分配 run 由 Worker 落终态，未分配 run 使用 CAS。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_web_api.routes.v1 import task_cancel


@pytest.fixture
def task(monkeypatch):
    resolved = SimpleNamespace(id=7)
    monkeypatch.setattr(task_cancel.scheduler_service, "get_task_by_id", AsyncMock(return_value=resolved))
    monkeypatch.setattr(task_cancel, "record_task_cancel_request", AsyncMock(return_value=True))
    return resolved


def _execution(*, worker_id, status=TaskStatus.RUNNING):
    return SimpleNamespace(run_id="run-1", worker_id=worker_id, status=status)


@pytest.mark.asyncio
async def test_assigned_run_is_not_cancelled_when_control_fails(monkeypatch, task):
    execution = _execution(worker_id=9)
    mark_cancelled = AsyncMock()
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=execution))
    monkeypatch.setattr(task_cancel, "try_send_task_cancel", AsyncMock(return_value=False))
    monkeypatch.setattr(task_cancel, "mark_task_run_cancelled", mark_cancelled)

    result = await task_cancel.cancel_latest_task_run("task-1", user_id=3)

    assert result is False
    mark_cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_assigned_run_waits_for_worker_result_after_control_succeeds(monkeypatch, task):
    execution = _execution(worker_id=9)
    mark_cancelled = AsyncMock(return_value=True)
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=execution))
    monkeypatch.setattr(task_cancel, "try_send_task_cancel", AsyncMock(return_value=True))
    monkeypatch.setattr(task_cancel, "mark_task_run_cancelled", mark_cancelled)

    result = await task_cancel.cancel_latest_task_run("task-1", user_id=3)

    assert result is True
    mark_cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_unassigned_run_goes_through_cas_cancel(monkeypatch, task):
    """P1-FN-01: 未分配 run 必须走 cancel_unassigned_run CAS（收敛 dispatch_status
    成为 Master 派发 fence），而不是只写 runtime 状态。"""
    execution = _execution(worker_id=None, status=TaskStatus.QUEUED)
    cas_cancel = AsyncMock(return_value=True)
    send_cancel = AsyncMock()
    mark_cancelled = AsyncMock()
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=execution))
    monkeypatch.setattr(task_cancel, "stop_unassigned_task_run", cas_cancel)
    monkeypatch.setattr(task_cancel, "try_send_task_cancel", send_cancel)
    monkeypatch.setattr(task_cancel, "mark_task_run_cancelled", mark_cancelled)

    result = await task_cancel.cancel_latest_task_run("task-1", user_id=3)

    assert result is True
    cas_cancel.assert_awaited_once_with(execution, 3)
    send_cancel.assert_not_awaited()
    mark_cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatching_without_worker_goes_through_cas_cancel(monkeypatch, task):
    execution = _execution(worker_id=None, status=TaskStatus.DISPATCHING)
    cas_cancel = AsyncMock(return_value=True)
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=execution))
    monkeypatch.setattr(task_cancel, "stop_unassigned_task_run", cas_cancel)
    send_cancel = AsyncMock()
    monkeypatch.setattr(task_cancel, "try_send_task_cancel", send_cancel)

    assert await task_cancel.cancel_latest_task_run("task-1", user_id=3) is True
    cas_cancel.assert_awaited_once_with(execution, 3)
    send_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_unassigned_cas_miss_falls_back_to_refreshed_execution(monkeypatch, task):
    """未分配 CAS 未命中（已被派发）→ 重读后按已分配路径继续。"""
    stale = _execution(worker_id=None, status=TaskStatus.QUEUED)
    refreshed = _execution(worker_id=9, status=TaskStatus.RUNNING)
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=stale))
    monkeypatch.setattr(task_cancel, "stop_unassigned_task_run", AsyncMock(return_value=False))
    monkeypatch.setattr(task_cancel.TaskRun, "get_or_none", AsyncMock(return_value=refreshed))
    send_cancel = AsyncMock(return_value=True)
    mark_cancelled = AsyncMock(return_value=True)
    monkeypatch.setattr(task_cancel, "try_send_task_cancel", send_cancel)
    monkeypatch.setattr(task_cancel, "mark_task_run_cancelled", mark_cancelled)

    result = await task_cancel.cancel_latest_task_run("task-1", user_id=3)

    assert result is True
    send_cancel.assert_awaited_once_with(refreshed, 3)
    mark_cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_unassigned_cas_miss_with_terminal_state_reports_conflict(monkeypatch, task):
    """无 Worker 的 CAS 未生效且 run 已进入其它终态时必须报告冲突。"""
    execution = _execution(worker_id=None, status=TaskStatus.QUEUED)
    final = SimpleNamespace(run_id="run-1", status=TaskStatus.SUCCESS)
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=execution))
    monkeypatch.setattr(task_cancel, "stop_unassigned_task_run", AsyncMock(return_value=False))
    monkeypatch.setattr(task_cancel.TaskRun, "get_or_none", AsyncMock(side_effect=[execution, final]))
    monkeypatch.setattr(task_cancel, "mark_task_run_cancelled", AsyncMock(return_value=False))

    result = await task_cancel.cancel_latest_task_run("task-1", user_id=3)

    assert result is False


@pytest.mark.asyncio
async def test_unassigned_cas_miss_with_cancelled_state_is_idempotent_success(monkeypatch, task):
    execution = _execution(worker_id=None, status=TaskStatus.QUEUED)
    final = SimpleNamespace(run_id="run-1", status=TaskStatus.CANCELLED)
    monkeypatch.setattr(task_cancel, "latest_cancellable_run", AsyncMock(return_value=execution))
    monkeypatch.setattr(task_cancel, "stop_unassigned_task_run", AsyncMock(return_value=False))
    monkeypatch.setattr(task_cancel.TaskRun, "get_or_none", AsyncMock(side_effect=[execution, final]))
    monkeypatch.setattr(task_cancel, "mark_task_run_cancelled", AsyncMock(return_value=False))

    result = await task_cancel.cancel_latest_task_run("task-1", user_id=3)

    assert result is True


@pytest.mark.asyncio
async def test_try_send_cancel_exposes_failure_as_false(monkeypatch):
    execution = SimpleNamespace(run_id="run-1", worker_id=9)
    monkeypatch.setattr(
        task_cancel,
        "send_task_cancel",
        AsyncMock(side_effect=RuntimeError("control unavailable")),
    )

    assert await task_cancel.try_send_task_cancel(execution, user_id=3) is False
