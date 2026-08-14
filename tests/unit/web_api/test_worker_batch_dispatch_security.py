import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import BatchDispatchResult
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from antcode_web_api.routes.v1 import workers
from fastapi import HTTPException, status


class _RowsQuery:
    def __init__(self, rows):
        self._rows = rows

    def only(self, *_fields):
        return self

    async def all(self):
        return self._rows


def _request(run_id: str = "run-7") -> workers.WorkerDispatchBatchRequest:
    return workers.WorkerDispatchBatchRequest(
        tasks=[
            {
                "project_id": "project-public-1",
                "task_id": 7,
                "run_id": run_id,
                "project_type": "rule",
            }
        ]
    )


def _make_run(
    run_task_id: int,
    *,
    run_status=TaskStatus.PENDING,
    dispatch_status=DispatchStatus.PENDING,
    runtime_status=None,
):
    return SimpleNamespace(
        run_id="run-7",
        task_id=run_task_id,
        status=run_status,
        dispatch_status=dispatch_status,
        runtime_status=runtime_status,
    )


def _patch_owned_project_and_task(monkeypatch, *, run_task_id: int, run=None):
    project_module = importlib.import_module("antcode_core.application.services.projects.project_service")
    project = SimpleNamespace(
        id=11,
        user_id=3,
        env_location="worker",
        worker_env_name="runtime-1",
    )
    task = SimpleNamespace(id=7, project_id=11, user_id=3)
    run = run if run is not None else _make_run(run_task_id)
    monkeypatch.setattr(
        project_module.project_service,
        "get_project_by_id",
        AsyncMock(return_value=project),
    )
    monkeypatch.setattr(workers.Task, "filter", lambda **_filters: _RowsQuery([task]))
    monkeypatch.setattr(workers.TaskRun, "filter", lambda **_filters: _RowsQuery([run]))


@pytest.mark.asyncio
async def test_batch_dispatch_rejects_foreign_run_before_dispatch(monkeypatch):
    services_module = importlib.import_module("antcode_core.application.services.workers")
    _patch_owned_project_and_task(monkeypatch, run_task_id=99)
    dispatch = AsyncMock()
    monkeypatch.setattr(services_module.worker_task_dispatcher, "dispatch_batch", dispatch)
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value="worker-1"))

    with pytest.raises(HTTPException) as exc_info:
        await workers.dispatch_batch_to_worker(_request(), SimpleNamespace(user_id=3))

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert "运行记录" in str(exc_info.value.detail)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_dispatch_passes_verified_scope_to_dispatcher(monkeypatch, active_scheduler_authority):
    services_module = importlib.import_module("antcode_core.application.services.workers")
    _patch_owned_project_and_task(monkeypatch, run_task_id=7)
    dispatch = AsyncMock(return_value=BatchDispatchResult(success=True))
    monkeypatch.setattr(services_module.worker_task_dispatcher, "dispatch_batch", dispatch)
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value="worker-1"))

    response = await workers.dispatch_batch_to_worker(_request(), SimpleNamespace(user_id=3))

    assert response.success is True
    task = dispatch.await_args.kwargs["tasks"][0]
    assert task["task_id"] == 7
    assert task["run_id"] == "run-7"
    assert task["runtime_env_name"] == "runtime-1"
    assert task["_dispatch_scope"] == {
        "run_id": "run-7",
        "task_id": 7,
        "project_id": 11,
        "owner_id": 3,
    }


# ---------------------------------------------------------------------------
# P1-FN-03 回归：批量分发不得重派终态/运行中/已取消的历史 run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        # (run_status, dispatch_status, runtime_status)
        # 已成功结束的 run（runtime 终态）
        (TaskStatus.SUCCESS, DispatchStatus.ACKED, RuntimeStatus.SUCCESS),
        # 正在运行的 run
        (TaskStatus.RUNNING, DispatchStatus.DISPATCHED, RuntimeStatus.RUNNING),
        # 已派出等待执行的 run（dispatch 已越过 PENDING）
        (TaskStatus.QUEUED, DispatchStatus.DISPATCHED, None),
        # 用户已取消的 run（cancel_unassigned_run 写 CANCELLED + dispatch FAILED）
        (TaskStatus.CANCELLED, DispatchStatus.FAILED, None),
    ],
)
@pytest.mark.asyncio
async def test_batch_dispatch_rejects_non_dispatchable_run_with_conflict(monkeypatch, case):
    run_status, dispatch_status, runtime_status = case
    services_module = importlib.import_module("antcode_core.application.services.workers")
    run = _make_run(7, run_status=run_status, dispatch_status=dispatch_status, runtime_status=runtime_status)
    _patch_owned_project_and_task(monkeypatch, run_task_id=7, run=run)
    dispatch = AsyncMock()
    monkeypatch.setattr(services_module.worker_task_dispatcher, "dispatch_batch", dispatch)
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value="worker-1"))

    with pytest.raises(HTTPException) as exc_info:
        await workers.dispatch_batch_to_worker(_request(), SimpleNamespace(user_id=3))

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    conflicts = exc_info.value.detail["conflicts"]
    assert conflicts[0]["run_id"] == "run-7"
    assert conflicts[0]["index"] == 0
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_dispatch_allows_redispatch_of_dispatch_failed_run(monkeypatch, active_scheduler_authority):
    """派发层判 FAILED 且 runtime 从未启动、未被取消的 run 允许显式重派。"""
    services_module = importlib.import_module("antcode_core.application.services.workers")
    run = _make_run(7, run_status=TaskStatus.FAILED, dispatch_status=DispatchStatus.FAILED, runtime_status=None)
    _patch_owned_project_and_task(monkeypatch, run_task_id=7, run=run)
    dispatch = AsyncMock(return_value=BatchDispatchResult(success=True))
    monkeypatch.setattr(services_module.worker_task_dispatcher, "dispatch_batch", dispatch)
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value="worker-1"))

    response = await workers.dispatch_batch_to_worker(_request(), SimpleNamespace(user_id=3))

    assert response.success is True
    dispatch.assert_awaited_once()
