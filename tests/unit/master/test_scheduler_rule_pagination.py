from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_master.control import scheduler_loop


def _rule_detail():
    return SimpleNamespace(
        pagination_config={"method": "url_pattern", "start_page": 1, "max_pages": 2},
        target_url="https://example.com/page/{}",
    )


@pytest.mark.asyncio
async def test_rule_pagination_is_dispatched_once_as_pending_base_run(monkeypatch):
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    submit_rule_task = AsyncMock(
        return_value={
            "success": True,
            "task_id": "remote-task-1",
            "worker_id": "worker-1",
            "worker_name": "Worker 1",
        }
    )
    monkeypatch.setattr(
        scheduler_loop.spider_task_dispatcher,
        "submit_rule_task",
        submit_rule_task,
    )

    result = await service._execute_rule_task(
        SimpleNamespace(id=1, name="task", execution_params={}, timeout_seconds=45, priority=7),
        SimpleNamespace(public_id="project-1"),
        _rule_detail(),
        SimpleNamespace(run_id="run-1", scheduler_fencing_token=7, result_data={}),
        target_worker=SimpleNamespace(public_id="worker-1"),
    )

    assert result["success"] is True
    assert result["distributed"] is True
    assert result["pending"] is True
    submit_rule_task.assert_awaited_once()
    assert submit_rule_task.await_args.kwargs["run_id"] == "run-1"
    assert submit_rule_task.await_args.kwargs["worker_id"] == "worker-1"
    assert submit_rule_task.await_args.kwargs["timeout"] == 45
    assert submit_rule_task.await_args.kwargs["priority"] == 7


@pytest.mark.asyncio
async def test_rule_dispatch_failure_is_exposed(monkeypatch):
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    submit_rule_task = AsyncMock(return_value={"success": False, "error": "dispatch failed"})
    monkeypatch.setattr(
        scheduler_loop.spider_task_dispatcher,
        "submit_rule_task",
        submit_rule_task,
    )

    result = await service._execute_rule_task(
        SimpleNamespace(id=1, name="task", execution_params={}, timeout_seconds=45, priority=7),
        SimpleNamespace(public_id="project-1"),
        _rule_detail(),
        SimpleNamespace(run_id="run-1", scheduler_fencing_token=7, result_data={}),
        target_worker=SimpleNamespace(public_id="worker-1"),
    )

    assert result["success"] is False
    assert result["error"] == "dispatch failed"


@pytest.mark.asyncio
async def test_pending_rule_dispatch_does_not_write_runtime_success(monkeypatch):
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    service._persist_result_fields = AsyncMock()
    update_dispatch = AsyncMock(return_value=True)
    update_runtime = AsyncMock(return_value=True)
    monkeypatch.setattr(scheduler_loop.execution_status_service, "update_dispatch_status", update_dispatch)
    monkeypatch.setattr(scheduler_loop.execution_status_service, "update_runtime_status", update_runtime)
    execution = SimpleNamespace(result_data={}, save=AsyncMock())
    result = {"success": True, "distributed": True, "pending": True}

    outcome = await service._record_dispatch_result(execution, "run-1", result)

    assert outcome == (None, True)
    service._persist_result_fields.assert_awaited_once_with("run-1", result)
    update_dispatch.assert_awaited_once()
    update_runtime.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_dispatch_success_without_pending_markers_is_rejected():
    service = scheduler_loop.SchedulerService()
    execution = SimpleNamespace(result_data={}, save=AsyncMock())

    with pytest.raises(RuntimeError, match="缺少 distributed=true,pending=true"):
        await service._record_dispatch_result(execution, "run-1", {"success": True})
