"""Internal service failures must not be reflected in API responses."""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_web_api.routes.v1 import base, branding, dashboard, project, runtime_access, runtimes, system_config, tasks
from fastapi import HTTPException, status

_INTERNAL_ERROR = "postgresql://internal-user:secret@db/private-table"


@dataclass(frozen=True)
class _CreateTaskFailureCase:
    failure: Exception
    expected_status: int
    expected_detail: str


@pytest.mark.asyncio
async def test_public_branding_hides_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(
        branding.system_config_service,
        "get_branding_config",
        MagicMock(side_effect=RuntimeError(_INTERNAL_ERROR)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await branding.get_public_branding_config()

    assert exc_info.value.detail == "获取品牌配置失败"


@pytest.mark.asyncio
async def test_dashboard_metrics_hides_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.unified_cache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        dashboard.system_metrics_service,
        "get_metrics",
        AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await dashboard.get_system_metrics(SimpleNamespace())

    assert exc_info.value.detail == "获取指标失败"


@pytest.mark.asyncio
async def test_system_config_hides_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(
        system_config.system_config_service,
        "get_all_configs",
        AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await system_config.get_all_configs(current_admin=SimpleNamespace())

    assert exc_info.value.detail == "获取系统配置失败"


@pytest.mark.asyncio
async def test_refresh_token_hides_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(base, "_resolve_refresh_token", MagicMock(return_value="refresh-token"))
    monkeypatch.setattr(base, "verify_refresh_token", AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)))

    with pytest.raises(HTTPException) as exc_info:
        await base.refresh_token(SimpleNamespace(), SimpleNamespace(), None)

    assert exc_info.value.detail == "refresh token 无效"


@pytest.mark.asyncio
async def test_update_code_project_hides_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(
        project.project_service,
        "update_code_config",
        AsyncMock(side_effect=RuntimeError(_INTERNAL_ERROR)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await project.update_code_config("project-1", SimpleNamespace(), current_user_id=7)

    assert exc_info.value.detail == "更新代码项目配置失败"


@pytest.mark.asyncio
async def test_runtime_worker_error_is_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "list_envs",
        AsyncMock(return_value={"success": False, "error": _INTERNAL_ERROR}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtimes.list_envs(
            "worker-1",
            current_user_id=7,
            current_user=SimpleNamespace(is_admin=True),
        )

    assert exc_info.value.detail == "获取环境失败"


@pytest.mark.asyncio
async def test_runtime_access_worker_error_is_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_access.runtime_control_service,
        "get_env",
        AsyncMock(return_value={"success": False, "error": _INTERNAL_ERROR}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtime_access.fetch_accessible_runtime(
            "worker-1",
            "env-1",
            SimpleNamespace(is_admin=True),
        )

    assert exc_info.value.detail == "获取环境失败"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        _CreateTaskFailureCase(ValueError(_INTERNAL_ERROR), status.HTTP_400_BAD_REQUEST, "任务配置无效"),
        _CreateTaskFailureCase(RuntimeError(_INTERNAL_ERROR), status.HTTP_500_INTERNAL_SERVER_ERROR, "创建任务失败"),
    ],
)
async def test_create_task_hides_service_error(monkeypatch, case: _CreateTaskFailureCase) -> None:
    task_data = SimpleNamespace(project_id="project-1", model_fields_set=set(), specified_worker_id=None)
    project = SimpleNamespace(id=9, type="code")
    monkeypatch.setattr(tasks.relation_service, "validate_project_user", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tasks.relation_service,
        "get_project_with_details",
        AsyncMock(return_value={"project": project}),
    )
    monkeypatch.setattr(tasks.scheduler_service, "create_task", AsyncMock(side_effect=case.failure))

    with pytest.raises(HTTPException) as exc_info:
        await tasks.create_task(task_data, SimpleNamespace(user_id=7))

    assert exc_info.value.status_code == case.expected_status
    assert exc_info.value.detail == case.expected_detail


@pytest.mark.asyncio
async def test_stop_task_hides_worker_transport_error(monkeypatch) -> None:
    execution = SimpleNamespace(
        run_id="run-1",
        worker_id=3,
        status=TaskStatus.RUNNING,
    )
    monkeypatch.setattr(tasks, "_get_stoppable_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(tasks, "is_unassigned_task_run", MagicMock(return_value=False))
    monkeypatch.setattr(
        tasks,
        "_try_send_stop_event_with_reason",
        AsyncMock(return_value=(False, _INTERNAL_ERROR)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await tasks.stop_task_execution("run-1", SimpleNamespace(user_id=7))

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == "取消指令发送失败，请重试"
