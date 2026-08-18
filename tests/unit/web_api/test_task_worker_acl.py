import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.task import TaskCreateRequest, TaskUpdateRequest
from antcode_web_api.routes.v1 import tasks
from fastapi import HTTPException

scheduler_module = importlib.import_module("antcode_core.application.services.scheduler.scheduler_service")
WORKER_INTERNAL_ID = 41


def _task_create(worker_id: str) -> TaskCreateRequest:
    return TaskCreateRequest(
        name="worker-acl-task",
        project_id="project-1",
        schedule_type="interval",
        interval_seconds=60,
        specified_worker_id=worker_id,
    )


@pytest.mark.asyncio
async def test_specified_worker_acl_checks_only_non_empty_explicit_bindings(monkeypatch) -> None:
    ensure_access = AsyncMock()
    monkeypatch.setattr(tasks, "ensure_worker_use_access", ensure_access)
    current_user = SimpleNamespace(user_id=7)

    await tasks._ensure_specified_worker_access(TaskUpdateRequest(), current_user)
    await tasks._ensure_specified_worker_access(TaskUpdateRequest(specified_worker_id=None), current_user)
    await tasks._ensure_specified_worker_access(TaskUpdateRequest(specified_worker_id="worker-1"), current_user)

    ensure_access.assert_awaited_once_with("worker-1", 7)


@pytest.mark.asyncio
async def test_task_create_preserves_worker_acl_forbidden(monkeypatch, http_request) -> None:
    monkeypatch.setattr(tasks.relation_service, "validate_project_user", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tasks.relation_service,
        "get_project_with_details",
        AsyncMock(return_value={"project": SimpleNamespace(id=1, type="code")}),
    )
    monkeypatch.setattr(
        tasks,
        "ensure_worker_use_access",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="无 Worker 访问权限")),
    )
    create_task = AsyncMock()
    monkeypatch.setattr(tasks.scheduler_service, "create_task", create_task)

    with pytest.raises(HTTPException) as exc_info:
        await tasks.create_task(_task_create("worker-foreign"), SimpleNamespace(user_id=7), http_request=http_request)

    assert exc_info.value.status_code == 403
    create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_update_rejects_worker_before_scheduler_mutation(monkeypatch, http_request) -> None:
    monkeypatch.setattr(
        tasks,
        "ensure_worker_use_access",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="无 Worker 访问权限")),
    )
    update_task = AsyncMock()
    monkeypatch.setattr(tasks.scheduler_service, "update_task", update_task)

    with pytest.raises(HTTPException) as exc_info:
        await tasks.update_task(
            "task-1",
            TaskUpdateRequest(specified_worker_id="worker-foreign"),
            SimpleNamespace(user_id=7),
            http_request=http_request,
        )

    assert exc_info.value.status_code == 403
    update_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_resolves_worker_public_id(monkeypatch) -> None:
    models = __import__("antcode_core.domain.models", fromlist=["Worker"])
    query = SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(id=WORKER_INTERNAL_ID)))
    monkeypatch.setattr(models.Worker, "filter", classmethod(lambda cls, **kwargs: query))

    result = await scheduler_module.scheduler_service._resolve_worker_internal_id("worker-public")

    assert result == WORKER_INTERNAL_ID
