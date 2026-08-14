"""Worker runtime mutation audit events."""

from dataclasses import dataclass

from antcode_core.application.services.audit import audit_service
from antcode_core.common.security.auth import TokenData
from antcode_core.domain.models.audit_log import AuditAction

from antcode_web_api.committed_audit import record_committed_audit


@dataclass(frozen=True)
class RuntimeAuditEvent:
    operation: str
    action: AuditAction
    resource_type: str
    worker_id: str
    env_name: str
    description: str


async def _record(event: RuntimeAuditEvent, current_user: TokenData) -> None:
    await record_committed_audit(
        event.operation,
        lambda: audit_service.log(
            action=event.action,
            resource_type=event.resource_type,
            resource_id=f"{event.worker_id}/{event.env_name}",
            resource_name=event.env_name,
            username=current_user.username,
            user_id=current_user.user_id,
            description=event.description,
        ),
    )


async def audit_runtime_create(worker_id: str, env_name: str, user: TokenData) -> None:
    await _record(
        RuntimeAuditEvent(
            "runtime_create",
            AuditAction.ENV_CREATE,
            "runtime",
            worker_id,
            env_name,
            f"创建 Worker 运行时: {worker_id}/{env_name}",
        ),
        user,
    )


async def audit_runtime_update(worker_id: str, env_name: str, user: TokenData) -> None:
    await _record(
        RuntimeAuditEvent(
            "runtime_update",
            AuditAction.CONFIG_UPDATE,
            "runtime",
            worker_id,
            env_name,
            f"更新 Worker 运行时元数据: {worker_id}/{env_name}",
        ),
        user,
    )


async def audit_runtime_delete(worker_id: str, env_name: str, user: TokenData) -> None:
    await _record(
        RuntimeAuditEvent(
            "runtime_delete",
            AuditAction.ENV_DELETE,
            "runtime",
            worker_id,
            env_name,
            f"删除 Worker 运行时: {worker_id}/{env_name}",
        ),
        user,
    )


async def audit_package_install(worker_id: str, env_name: str, user: TokenData) -> None:
    await _record(
        RuntimeAuditEvent(
            "runtime_packages_install",
            AuditAction.CONFIG_UPDATE,
            "runtime_packages",
            worker_id,
            env_name,
            f"安装 Worker 运行时依赖: {worker_id}/{env_name}",
        ),
        user,
    )


async def audit_package_uninstall(worker_id: str, env_name: str, user: TokenData) -> None:
    await _record(
        RuntimeAuditEvent(
            "runtime_packages_uninstall",
            AuditAction.CONFIG_UPDATE,
            "runtime_packages",
            worker_id,
            env_name,
            f"卸载 Worker 运行时依赖: {worker_id}/{env_name}",
        ),
        user,
    )


__all__ = [
    "audit_package_install",
    "audit_package_uninstall",
    "audit_runtime_create",
    "audit_runtime_delete",
    "audit_runtime_update",
]
