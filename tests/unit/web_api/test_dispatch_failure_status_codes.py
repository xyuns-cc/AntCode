"""容量不足回 503、其余失败回 500——单任务与批量两条路由口径必须一致。

从前两条路由都只有 500：调用方分不出"稍后重试"和"我们坏了"，重试逻辑与告警都没法写。
映射只看 ``error_code``，**不看** ``result.error`` 文案，也不靠 ``worker_id is None``
这种隐式判据（把契约编进可空字段，下一个人给容量失败补上 worker_id 就静默翻车）。

证伪方式：把 ``dispatch_failure_response`` 里的 ``CAPACITY_ERROR_CODES`` 判断删掉，
503 那两条立刻变红；把非容量码也算进容量集合，500 那两条立刻变红。
"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.dispatch_error_codes import (
    DISPATCH_NO_CAPACITY,
    DISPATCH_QUEUE_WRITE_FAILED,
    DISPATCH_WORKER_OFFLINE,
)
from antcode_core.application.services.workers.worker_dispatcher import BatchDispatchResult, DispatchResult
from antcode_web_api.routes.v1 import workers
from fastapi import HTTPException, status

_BATCH_TASKS = [{"project_id": "project-1", "task_id": 5, "run_id": "run-1"}]
_CAPACITY_DETAIL = "暂无可用 Worker，请稍后重试"


async def _batch_dispatch_failure(monkeypatch, error_code: str) -> HTTPException:
    services_module = importlib.import_module("antcode_core.application.services.workers")
    guard_module = importlib.import_module("antcode_web_api.routes.v1.worker_dispatch_guard")
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value=None))
    monkeypatch.setattr(guard_module, "authorize_batch_dispatch_tasks", AsyncMock(return_value=_BATCH_TASKS))
    monkeypatch.setattr(
        services_module.worker_task_dispatcher,
        "dispatch_batch",
        AsyncMock(return_value=BatchDispatchResult(success=False, error="内部原因", error_code=error_code)),
    )
    request = workers.WorkerDispatchBatchRequest(tasks=_BATCH_TASKS)

    with pytest.raises(HTTPException) as exc_info:
        await workers.dispatch_batch_to_worker(request, SimpleNamespace(user_id=1))
    return exc_info.value


async def _single_dispatch_failure(monkeypatch, error_code: str) -> HTTPException:
    services_module = importlib.import_module("antcode_core.application.services.workers")
    project_module = importlib.import_module("antcode_core.application.services.projects.project_service")
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value=None))
    monkeypatch.setattr(
        project_module.project_service,
        "get_project_by_id",
        AsyncMock(return_value=SimpleNamespace(id=9, env_location=None, worker_env_name=None)),
    )
    monkeypatch.setattr(
        workers.Task,
        "filter",
        lambda **_filters: SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(id=5))),
    )
    monkeypatch.setattr(workers.TaskRun, "create", AsyncMock(return_value=SimpleNamespace(save=AsyncMock())))
    monkeypatch.setattr(
        services_module.worker_task_dispatcher,
        "dispatch_task",
        AsyncMock(return_value=DispatchResult(success=False, error="内部原因", error_code=error_code)),
    )
    request = workers.WorkerDispatchTaskRequest(project_id="project-1", task_id=5)

    with pytest.raises(HTTPException) as exc_info:
        await workers.dispatch_task_to_worker(request, SimpleNamespace(user_id=1))
    return exc_info.value


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [DISPATCH_NO_CAPACITY, DISPATCH_WORKER_OFFLINE])
async def test_batch_capacity_failure_is_retryable_503(monkeypatch, active_scheduler_authority, error_code) -> None:
    failure = await _batch_dispatch_failure(monkeypatch, error_code)

    assert failure.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert failure.detail == _CAPACITY_DETAIL


@pytest.mark.asyncio
async def test_batch_queue_write_failure_stays_500(monkeypatch, active_scheduler_authority) -> None:
    failure = await _batch_dispatch_failure(monkeypatch, DISPATCH_QUEUE_WRITE_FAILED)

    assert failure.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert failure.detail == "批量任务分发失败"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [DISPATCH_NO_CAPACITY, DISPATCH_WORKER_OFFLINE])
async def test_single_task_capacity_failure_is_retryable_503(
    monkeypatch, active_scheduler_authority, error_code
) -> None:
    """单任务路由与批量路由同源同码：容量不足在这条路径上同样不该报 500。"""
    failure = await _single_dispatch_failure(monkeypatch, error_code)

    assert failure.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert failure.detail == _CAPACITY_DETAIL


@pytest.mark.asyncio
async def test_single_task_queue_write_failure_stays_500(monkeypatch, active_scheduler_authority) -> None:
    failure = await _single_dispatch_failure(monkeypatch, DISPATCH_QUEUE_WRITE_FAILED)

    assert failure.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert failure.detail == "任务分发失败"
