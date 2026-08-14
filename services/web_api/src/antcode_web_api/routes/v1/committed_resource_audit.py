"""Audit mappings for resource mutations that have already committed."""

from collections.abc import Awaitable, Callable
from typing import Any

from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.users.user_service import user_service
from antcode_core.domain.models.audit_log import AuditAction

from antcode_web_api.committed_audit import record_committed_audit


def _client_ip(request: Any) -> str | None:
    return request.client.host if request.client else None


async def _record_with_username(
    operation: str,
    user_id: int,
    audit_write: Callable[[str], Awaitable[Any]],
) -> None:
    async def write() -> None:
        user = await user_service.get_user_by_id(user_id)
        await audit_write(user.username if user else "unknown")

    await record_committed_audit(operation, write)


async def audit_project_created(request: Any, current_user: Any, project: Any) -> None:
    await _record_with_username(
        "project_create",
        current_user.user_id,
        lambda username: audit_service.log_project_action(
            action=AuditAction.PROJECT_CREATE,
            username=username,
            project_id=project.id,
            project_name=project.name,
            user_id=current_user.user_id,
            ip_address=_client_ip(request),
            description=f"创建项目: {project.name} (类型: {project.type})",
        ),
    )


async def audit_project_deleted(
    request: Any,
    current_user: Any,
    *,
    project: Any,
    project_name: str,
) -> None:
    await _record_with_username(
        "project_delete",
        current_user.user_id,
        lambda username: audit_service.log_project_action(
            action=AuditAction.PROJECT_DELETE,
            username=username,
            project_id=project.id if project else 0,
            project_name=project_name,
            user_id=current_user.user_id,
            ip_address=_client_ip(request),
            description=f"删除项目: {project_name}",
        ),
    )


async def audit_worker_created(request: Any, current_user: Any, worker: Any) -> None:
    await _record_with_username(
        "worker_create",
        current_user.user_id,
        lambda username: audit_service.log(
            action=AuditAction.WORKER_CREATE,
            resource_type="worker",
            username=username,
            resource_id=worker.public_id,
            resource_name=worker.name,
            user_id=current_user.user_id,
            ip_address=_client_ip(request),
            description=f"创建 Worker: {worker.name}",
        ),
    )


async def audit_worker_deleted(
    request: Any,
    current_user: Any,
    *,
    worker_id: str,
    worker_name: str,
) -> None:
    await _record_with_username(
        "worker_delete",
        current_user.user_id,
        lambda username: audit_service.log(
            action=AuditAction.WORKER_DELETE,
            resource_type="worker",
            username=username,
            resource_id=worker_id,
            resource_name=worker_name,
            user_id=current_user.user_id,
            ip_address=_client_ip(request),
            description=f"删除 Worker: {worker_name}",
        ),
    )


async def audit_user_created(request: Any, admin: Any, user: Any) -> None:
    await record_committed_audit(
        "user_create",
        lambda: audit_service.log_user_action(
            action=AuditAction.USER_CREATE,
            operator_username=admin.username,
            target_user_id=user.id,
            target_username=user.username,
            operator_id=admin.id,
            ip_address=_client_ip(request),
            new_value={"username": user.username, "email": user.email, "is_admin": user.is_admin},
            description=f"创建用户: {user.username}",
        ),
    )


async def audit_user_role_changed(
    request: Any,
    admin: Any,
    target: Any,
    *,
    old_role: str,
    old_is_admin: bool,
) -> None:
    await record_committed_audit(
        "user_role_update",
        lambda: audit_service.log_user_action(
            action=AuditAction.USER_UPDATE,
            operator_username=admin.username,
            target_user_id=target.id,
            target_username=target.username,
            operator_id=admin.id,
            ip_address=_client_ip(request),
            old_value={"role": old_role, "is_admin": old_is_admin},
            new_value={"role": target.role.value, "is_admin": target.is_admin},
            description=f"修改用户角色: {target.username} {old_role} -> {target.role.value}",
        ),
    )


async def audit_user_updated(
    request: Any,
    operator: Any,
    user: Any,
    *,
    operator_username: str,
    old_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
    description: str,
) -> None:
    await record_committed_audit(
        "user_update",
        lambda: audit_service.log_user_action(
            action=AuditAction.USER_UPDATE,
            operator_username=operator_username,
            target_user_id=user.id,
            target_username=user.username,
            operator_id=operator.id,
            ip_address=_client_ip(request),
            old_value=old_snapshot,
            new_value=new_snapshot,
            description=description,
        ),
    )


async def audit_user_deleted(request: Any, admin: Any, target: Any) -> None:
    await record_committed_audit(
        "user_delete",
        lambda: audit_service.log_user_action(
            action=AuditAction.USER_DELETE,
            operator_username=admin.username,
            target_user_id=target.id,
            target_username=target.username,
            operator_id=admin.id,
            ip_address=_client_ip(request),
            description=f"删除用户: {target.username}",
        ),
    )


__all__ = [
    "audit_project_created",
    "audit_project_deleted",
    "audit_user_created",
    "audit_user_deleted",
    "audit_user_role_changed",
    "audit_user_updated",
    "audit_worker_created",
    "audit_worker_deleted",
]
