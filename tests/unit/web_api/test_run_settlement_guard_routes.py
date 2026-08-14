from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.run_settlement_guard import (
    RunSettlementGuardUnavailable,
    RunSettlementPendingError,
)
from antcode_web_api.routes.v1 import project, tasks, tasks_batch
from fastapi import HTTPException, status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (RunSettlementPendingError("执行仍在结算"), 409),
        (RunSettlementGuardUnavailable("执行结算状态服务不可用"), 503),
    ],
)
async def test_task_delete_maps_settlement_errors(monkeypatch, error, expected_status) -> None:
    monkeypatch.setattr(tasks.scheduler_service, "delete_task", AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as exc_info:
        await tasks.delete_task("task-1", SimpleNamespace(user_id=1))

    assert exc_info.value.status_code == expected_status


@pytest.mark.asyncio
async def test_project_delete_maps_guard_unavailable_to_503(monkeypatch) -> None:
    existing = SimpleNamespace(id=1, name="project")
    monkeypatch.setattr(project.project_service, "get_project_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(
        project.project_service,
        "delete_project",
        AsyncMock(side_effect=RunSettlementGuardUnavailable("执行结算状态服务不可用")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await project.delete_project(
            "project-1",
            SimpleNamespace(client=None),
            current_user_id=1,
            current_user=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == "执行结算状态服务不可用"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["batch-delete", "batch-action"])
async def test_task_batch_does_not_swallow_guard_unavailable(monkeypatch, operation) -> None:
    monkeypatch.setattr(
        tasks_batch.scheduler_service,
        "delete_task",
        AsyncMock(side_effect=RunSettlementGuardUnavailable("执行结算状态服务不可用")),
    )
    user = SimpleNamespace(user_id=1)

    with pytest.raises(HTTPException) as exc_info:
        if operation == "batch-delete":
            await tasks_batch.batch_delete_tasks({"task_ids": ["task-1"]}, user)
        else:
            request = tasks_batch.TaskBatchRequest(task_ids=["task-1"], action="delete")
            await tasks_batch.batch_operate_tasks(request, user)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_project_batch_does_not_swallow_guard_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        project.project_service,
        "delete_project",
        AsyncMock(side_effect=RunSettlementGuardUnavailable("执行结算状态服务不可用")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await project.batch_delete_projects({"project_ids": ["project-1"]}, current_user_id=1)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
