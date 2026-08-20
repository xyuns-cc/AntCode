"""这些写操作过去一条审计都不写，改密甚至完全不留痕。

用例走**真实路由 handler**（不是直接调 audit 辅助函数——那样测不到"路由从不
发出审计"这个缺陷本身），并断言 **真表 audit_logs 里真的多出了对应的行**：
``audit_service.log`` 不被 mock，被 mock 的只有 handler 下游的业务写入。
把任何一处 ``await audit_*`` 摘掉，对应用例立刻查不到行而变红。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.config import settings
from antcode_core.domain.models.audit_log import AuditAction, AuditLog
from antcode_core.domain.schemas.task import TaskUpdateRequest
from antcode_core.domain.schemas.user import (
    UserAdminPasswordUpdateRequest,
    UserPasswordUpdateRequest,
)
from antcode_core.domain.schemas.worker import WorkerUpdateRequest
from antcode_web_api.routes.v1 import project as project_routes
from antcode_web_api.routes.v1 import tasks as task_routes
from antcode_web_api.routes.v1 import tasks_execute, tasks_runs, workers_crud
from antcode_web_api.routes.v1 import users_password as user_routes
from fastapi import HTTPException

OPERATOR = SimpleNamespace(user_id=7, username="ops")
OPERATOR_IP = "127.0.0.1"
PLAINTEXT_CHANGE = UserPasswordUpdateRequest(old_password="Old#12345", new_password="Strong#123")


@pytest.fixture
def plaintext_password_allowed(monkeypatch):
    """本文件验证的是"审计有没有落行"，不是口令怎么过线。

    改密接口默认要求密文提交，这里显式放行明文，免得审计用例被传输策略连坐——
    否则策略一改，这批用例会以"审计没落行"的假象变红。
    """
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_REQUIRED", False)


async def _only_log(action: AuditAction) -> AuditLog:
    rows = await AuditLog.filter(action=action).all()
    assert len(rows) == 1, f"{action} 应恰好留下 1 条审计, 实际 {len(rows)}"
    return rows[0]


@pytest.mark.asyncio
async def test_project_update_writes_audit(audit_table, http_request, monkeypatch) -> None:
    updated = SimpleNamespace(id=11, name="proj-a")
    monkeypatch.setattr(
        project_routes.unified_project_service,
        "update_project_unified",
        AsyncMock(return_value=updated),
    )
    monkeypatch.setattr(project_routes, "create_project_response", lambda value: {"name": value.name})
    monkeypatch.setattr(project_routes, "_attach_project_detail_info", AsyncMock())
    payload = project_routes.UnifiedProjectUpdateRequest(name="proj-a", description="改过了")

    await project_routes.update_project("proj-public", payload, 7, http_request=http_request, current_user=OPERATOR)

    row = await _only_log(AuditAction.PROJECT_UPDATE)
    assert row.resource_type == "project"
    assert row.resource_name == "proj-a"
    assert row.username == "ops"
    assert row.ip_address == OPERATOR_IP
    assert row.new_value == {"changed_fields": ["description", "name"]}


@pytest.mark.asyncio
async def test_task_create_writes_audit(audit_table, http_request, monkeypatch) -> None:
    created = SimpleNamespace(public_id="task-public", name="nightly")
    monkeypatch.setattr(task_routes.relation_service, "validate_project_user", AsyncMock(return_value=True))
    monkeypatch.setattr(
        task_routes.relation_service,
        "get_project_with_details",
        AsyncMock(return_value={"project": SimpleNamespace(id=9, type="code")}),
    )
    monkeypatch.setattr(task_routes.scheduler_service, "create_task", AsyncMock(return_value=created))
    monkeypatch.setattr(task_routes, "create_task_response", lambda value: {"name": value.name})
    task_data = SimpleNamespace(project_id="proj-public", model_fields_set=set(), specified_worker_id=None)

    await task_routes.create_task(task_data, OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.TASK_CREATE)
    assert row.resource_type == "task"
    assert row.resource_id == "task-public"
    assert row.resource_name == "nightly"
    assert row.ip_address == OPERATOR_IP


@pytest.mark.asyncio
async def test_task_update_writes_audit(audit_table, http_request, monkeypatch) -> None:
    updated = SimpleNamespace(public_id="task-public", name="nightly")
    monkeypatch.setattr(task_routes, "_ensure_specified_worker_access", AsyncMock())
    monkeypatch.setattr(task_routes.scheduler_service, "update_task", AsyncMock(return_value=updated))
    monkeypatch.setattr(task_routes, "create_task_response", lambda value: {"name": value.name})

    await task_routes.update_task(
        "task-public",
        TaskUpdateRequest(name="nightly", is_active=False),
        OPERATOR,
        http_request=http_request,
    )

    row = await _only_log(AuditAction.TASK_UPDATE)
    assert row.resource_id == "task-public"
    assert "is_active" in (row.description or "")
    assert "name" in (row.description or "")


@pytest.mark.asyncio
async def test_task_delete_writes_audit_with_name_read_before_deletion(
    audit_table,
    http_request,
    monkeypatch,
) -> None:
    doomed = SimpleNamespace(public_id="task-public", name="nightly")
    monkeypatch.setattr(task_routes.scheduler_service, "get_task_by_id", AsyncMock(return_value=doomed))
    monkeypatch.setattr(task_routes.scheduler_service, "delete_task", AsyncMock(return_value=True))

    await task_routes.delete_task("task-public", OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.TASK_DELETE)
    # 名字只能在删除前读到；写成 public_id 就等于审计里没有可读线索。
    assert row.resource_name == "nightly"
    assert row.resource_id == "task-public"


@pytest.mark.asyncio
async def test_worker_update_writes_audit(audit_table, http_request, monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1", name="worker-ui-001")
    monkeypatch.setattr(workers_crud.worker_service, "update_worker", AsyncMock(return_value=worker))

    await workers_crud.update_worker(
        "worker-1",
        WorkerUpdateRequest(description="probe"),
        http_request=http_request,
        current_user=OPERATOR,
        worker_to_response=lambda value: {"id": value.public_id},
    )

    row = await _only_log(AuditAction.WORKER_UPDATE)
    assert row.resource_type == "worker"
    assert row.resource_name == "worker-ui-001"
    assert row.new_value == {"changed_fields": ["description"]}


@pytest.mark.asyncio
async def test_self_password_change_writes_audit(
    audit_table, http_request, monkeypatch, *, plaintext_password_allowed
) -> None:
    target = SimpleNamespace(id=7, username="ops")
    monkeypatch.setattr(user_routes.user_service, "update_user_password", AsyncMock(return_value=target))

    await user_routes.change_password(PLAINTEXT_CHANGE, OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.PASSWORD_CHANGE)
    assert row.resource_type == "user"
    assert row.resource_id == "7"
    assert row.new_value == {"reset_by_admin": False}
    # 审计里绝不能出现任何口令内容
    assert "password" not in (row.description or "").lower()


@pytest.mark.asyncio
async def test_admin_password_reset_writes_audit(
    audit_table, http_request, monkeypatch, *, plaintext_password_allowed
) -> None:
    target = SimpleNamespace(id=42, username="uiw4tester")
    monkeypatch.setattr(user_routes.user_service, "reset_user_password", AsyncMock(return_value=target))

    await user_routes.reset_user_password(
        "target-public",
        UserAdminPasswordUpdateRequest(new_password="Strong#123"),
        OPERATOR,
        http_request=http_request,
    )

    row = await _only_log(AuditAction.PASSWORD_CHANGE)
    assert row.resource_name == "uiw4tester"
    assert row.new_value == {"reset_by_admin": True}
    assert "Strong#123" not in (row.description or "")


@pytest.mark.asyncio
async def test_own_password_endpoint_writes_audit(
    audit_table, http_request, monkeypatch, *, plaintext_password_allowed
) -> None:
    target = SimpleNamespace(id=7, username="ops")
    monkeypatch.setattr(user_routes.user_service, "get_user_by_public_id", AsyncMock(return_value=target))
    monkeypatch.setattr(user_routes.user_service, "update_user_password", AsyncMock(return_value=target))

    await user_routes.update_user_password("self-public", PLAINTEXT_CHANGE, OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.PASSWORD_CHANGE)
    assert row.resource_id == "7"
    assert row.new_value == {"reset_by_admin": False}


@pytest.mark.asyncio
async def test_manual_trigger_writes_audit_with_run_id(audit_table, http_request, monkeypatch) -> None:
    """手动触发是"人让系统跑东西"，run_id 必须留在审计里才能往下追。"""
    monkeypatch.setattr(tasks_execute, "_acquire_trigger_dedup_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tasks_execute.scheduler_service,
        "trigger_task_by_user",
        AsyncMock(return_value="run-abc"),
    )

    await tasks_execute.trigger_task("task-public", OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.TASK_EXECUTE)
    assert row.resource_id == "task-public"
    assert "run-abc" in (row.description or "")


@pytest.mark.asyncio
async def test_toggle_writes_task_update_audit(audit_table, http_request, monkeypatch) -> None:
    """启用/禁用走的是另一个路由，同样是改 is_active，不能漏审计。"""
    toggled = SimpleNamespace(public_id="task-public", name="nightly")
    monkeypatch.setattr(tasks_execute.scheduler_service, "update_task", AsyncMock(return_value=toggled))

    await tasks_execute.toggle_task(
        "task-public",
        tasks_execute.TaskToggleRequest(enabled=False),
        OPERATOR,
        http_request=http_request,
        create_task_response=lambda value: {"name": value.name},
    )

    row = await _only_log(AuditAction.TASK_UPDATE)
    assert "is_active" in (row.description or "")


@pytest.mark.asyncio
async def test_accepted_stop_writes_audit(audit_table, http_request, monkeypatch) -> None:
    monkeypatch.setattr(
        tasks_runs,
        "_perform_task_stop",
        AsyncMock(return_value=SimpleNamespace(data={"status": "cancel_requested"})),
    )

    await tasks_runs.stop_task_execution("run-1", OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.TASK_STOP)
    assert row.resource_id == "run-1"


@pytest.mark.asyncio
async def test_rejected_stop_writes_no_audit(audit_table, http_request, monkeypatch) -> None:
    """没停成就不能留下"已停止"的记录。"""
    monkeypatch.setattr(
        tasks_runs,
        "_perform_task_stop",
        AsyncMock(side_effect=HTTPException(status_code=503, detail="取消指令发送失败，请重试")),
    )

    with pytest.raises(HTTPException):
        await tasks_runs.stop_task_execution("run-1", OPERATOR, http_request=http_request)

    assert await AuditLog.filter(action=AuditAction.TASK_STOP).count() == 0
