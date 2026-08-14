import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import (
    BatchDispatchResult,
    DispatchResult,
)
from antcode_core.common.exceptions import RedisConnectionError
from antcode_core.domain.models import UserRole
from antcode_core.domain.schemas.user import UserCreateRequest, UserUpdateRequest
from antcode_web_api import deps
from antcode_web_api.routes.v1 import runs, users, workers
from fastapi import HTTPException, status
from tortoise.exceptions import IntegrityError

_INTERNAL_ERROR = "postgresql://internal-user:secret@db/private-table"


@pytest.mark.asyncio
async def test_auth_dependency_hides_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_token_user", AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)))

    with pytest.raises(HTTPException) as exc_info:
        await deps.get_current_user(SimpleNamespace(credentials="token"))

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "认证失败"
    assert _INTERNAL_ERROR not in exc_info.value.detail


@pytest.mark.asyncio
async def test_cancel_run_hides_worker_transport_error(monkeypatch) -> None:
    execution = SimpleNamespace(run_id="run-1", worker_id=7)
    monkeypatch.setattr(runs, "_get_cancellable_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(runs, "_record_assigned_cancel_request", AsyncMock(return_value=True))
    monkeypatch.setattr(runs, "_write_worker_cancel_event", AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)))

    with pytest.raises(HTTPException) as exc_info:
        await runs.cancel_run("run-1", SimpleNamespace(user_id=3))

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == "取消指令发送失败，请稍后重试"
    assert _INTERNAL_ERROR not in exc_info.value.detail


@pytest.mark.asyncio
async def test_user_list_hides_database_error(monkeypatch) -> None:
    monkeypatch.setattr(users.user_service, "get_users_list", AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)))

    with pytest.raises(HTTPException) as exc_info:
        await users.get_users_list(current_admin=SimpleNamespace(user_id=1))

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "获取用户列表失败"


@pytest.mark.asyncio
async def test_unknown_create_user_conflict_is_sanitized(monkeypatch) -> None:
    admin = SimpleNamespace(id=1, username="root")
    monkeypatch.setattr(users.user_service, "get_user_by_id", AsyncMock(return_value=admin))
    monkeypatch.setattr(users.user_service, "create_user", AsyncMock(side_effect=IntegrityError(_INTERNAL_ERROR)))
    request = UserCreateRequest(username="alice", password="long-password", is_admin=False)

    with pytest.raises(HTTPException) as exc_info:
        await users.create_user(request, SimpleNamespace(client=None), SimpleNamespace(user_id=1))

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == "用户数据冲突"


@pytest.mark.asyncio
async def test_unknown_update_user_conflict_is_sanitized(monkeypatch) -> None:
    current = SimpleNamespace(id=1, username="alice", is_admin=False)
    target = SimpleNamespace(
        id=1,
        username="alice",
        email=None,
        is_active=True,
        is_admin=False,
        role=UserRole.USER,
    )
    monkeypatch.setattr(users.user_service, "get_user_by_id", AsyncMock(return_value=current))
    monkeypatch.setattr(users.user_service, "get_user_by_public_id", AsyncMock(return_value=target))
    monkeypatch.setattr(users.user_service, "update_user", AsyncMock(side_effect=IntegrityError(_INTERNAL_ERROR)))

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            "user-1",
            UserUpdateRequest(email="alice@example.com"),
            SimpleNamespace(client=None),
            SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == "用户数据冲突"


@pytest.mark.asyncio
async def test_direct_registration_hides_redis_connection_error(monkeypatch) -> None:
    redis_module = importlib.import_module("antcode_core.infrastructure.redis")
    monkeypatch.setattr(workers.settings, "REDIS_URL", "redis://configured")
    monkeypatch.setattr(workers.settings, "REDIS_ACL_ENABLED", False)
    monkeypatch.setattr(
        redis_module,
        "get_redis_client",
        AsyncMock(side_effect=RedisConnectionError(_INTERNAL_ERROR)),
    )
    request = workers.WorkerRegisterDirectRequest(worker_id="worker-1", proof="proof")

    with pytest.raises(HTTPException) as exc_info:
        await workers.register_direct_worker(request)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == "Direct 注册依赖服务暂不可用"


@pytest.mark.asyncio
async def test_dispatch_failure_hides_dispatcher_error(monkeypatch, active_scheduler_authority) -> None:
    services_module = importlib.import_module("antcode_core.application.services.workers")
    project_module = importlib.import_module("antcode_core.application.services.projects.project_service")
    project = SimpleNamespace(id=9, env_location=None, worker_env_name=None)
    task = SimpleNamespace(id=5)
    task_run = SimpleNamespace(save=AsyncMock())
    query = SimpleNamespace(first=AsyncMock(return_value=task))
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value=None))
    monkeypatch.setattr(project_module.project_service, "get_project_by_id", AsyncMock(return_value=project))
    monkeypatch.setattr(workers.Task, "filter", lambda **_filters: query)
    monkeypatch.setattr(workers.TaskRun, "create", AsyncMock(return_value=task_run))
    monkeypatch.setattr(
        services_module.worker_task_dispatcher,
        "dispatch_task",
        AsyncMock(return_value=DispatchResult(success=False, error=_INTERNAL_ERROR)),
    )
    request = workers.WorkerDispatchTaskRequest(project_id="project-1", task_id=5)

    with pytest.raises(HTTPException) as exc_info:
        await workers.dispatch_task_to_worker(request, SimpleNamespace(user_id=1))

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "任务分发失败"


@pytest.mark.asyncio
async def test_batch_dispatch_failure_hides_dispatcher_error(monkeypatch, active_scheduler_authority) -> None:
    services_module = importlib.import_module("antcode_core.application.services.workers")
    guard_module = importlib.import_module("antcode_web_api.routes.v1.worker_dispatch_guard")
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value=None))
    monkeypatch.setattr(
        guard_module,
        "authorize_batch_dispatch_tasks",
        AsyncMock(return_value=[{"project_id": "project-1", "task_id": 5, "run_id": "run-1"}]),
    )
    monkeypatch.setattr(
        services_module.worker_task_dispatcher,
        "dispatch_batch",
        AsyncMock(return_value=BatchDispatchResult(success=False, error=_INTERNAL_ERROR)),
    )
    request = workers.WorkerDispatchBatchRequest(tasks=[{"project_id": "project-1", "task_id": 5, "run_id": "run-1"}])

    with pytest.raises(HTTPException) as exc_info:
        await workers.dispatch_batch_to_worker(request, SimpleNamespace(user_id=1))

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "批量任务分发失败"
