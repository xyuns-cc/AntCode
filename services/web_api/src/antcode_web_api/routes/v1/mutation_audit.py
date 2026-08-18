"""Audit mappings for mutations whose ``AuditAction`` existed but was never emitted.

与 ``committed_resource_audit`` 的差别只有取操作者身份的方式：这里的调用点都已
持有 ``TokenData``（含 ``username``），直接用它即可，不再为写一条审计而多查一次
users 表——审计不该给主路径加额外往返。写入失败仍走 ``record_committed_audit``：
计数 + CRITICAL 日志，绝不静默吞掉，也绝不把审计故障伪装成业务失败。
"""

from typing import Any

from antcode_core.application.services.audit import audit_service
from antcode_core.domain.models.audit_log import AuditAction

from antcode_web_api.committed_audit import client_ip, record_committed_audit


async def audit_project_updated(request: Any, operator: Any, project: Any, *, changed_fields: list[str]) -> None:
    await record_committed_audit(
        "project_update",
        lambda: audit_service.log_project_action(
            action=AuditAction.PROJECT_UPDATE,
            username=operator.username,
            project_id=project.id,
            project_name=project.name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            new_value={"changed_fields": changed_fields},
            description=f"更新项目: {project.name}",
        ),
    )


async def audit_task_created(request: Any, operator: Any, task: Any) -> None:
    await record_committed_audit(
        "task_create",
        lambda: audit_service.log_task_action(
            action=AuditAction.TASK_CREATE,
            username=operator.username,
            task_id=task.public_id,
            task_name=task.name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            description=f"创建任务: {task.name}",
        ),
    )


async def audit_task_updated(request: Any, operator: Any, task: Any, *, changed_fields: list[str]) -> None:
    await record_committed_audit(
        "task_update",
        lambda: audit_service.log_task_action(
            action=AuditAction.TASK_UPDATE,
            username=operator.username,
            task_id=task.public_id,
            task_name=task.name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            description=f"更新任务: {task.name} (字段: {', '.join(changed_fields) or '无'})",
        ),
    )


async def audit_task_deleted(request: Any, operator: Any, *, task_id: str, task_name: str) -> None:
    await record_committed_audit(
        "task_delete",
        lambda: audit_service.log_task_action(
            action=AuditAction.TASK_DELETE,
            username=operator.username,
            task_id=task_id,
            task_name=task_name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            description=f"删除任务: {task_name}",
        ),
    )


async def audit_task_executed(request: Any, operator: Any, *, task_id: str, run_id: str | None) -> None:
    """人工触发执行。定时触发由 Master 自己发起，不是人的动作，不在这里留痕。"""
    await record_committed_audit(
        "task_execute",
        lambda: audit_service.log_task_action(
            action=AuditAction.TASK_EXECUTE,
            username=operator.username,
            task_id=task_id,
            task_name=task_id,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            description=f"手动触发任务执行: {task_id} (run_id: {run_id or '未返回'})",
        ),
    )


async def audit_task_stopped(request: Any, operator: Any, *, run_id: str) -> None:
    await record_committed_audit(
        "task_stop",
        lambda: audit_service.log_task_action(
            action=AuditAction.TASK_STOP,
            username=operator.username,
            task_id=run_id,
            task_name=run_id,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            description=f"停止任务执行: run_id={run_id}",
        ),
    )


async def audit_worker_updated(request: Any, operator: Any, worker: Any, *, changed_fields: list[str]) -> None:
    await record_committed_audit(
        "worker_update",
        lambda: audit_service.log(
            action=AuditAction.WORKER_UPDATE,
            resource_type="worker",
            username=operator.username,
            resource_id=worker.public_id,
            resource_name=worker.name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            new_value={"changed_fields": changed_fields},
            description=f"更新 Worker: {worker.name}",
        ),
    )


async def audit_password_changed(request: Any, operator: Any, *, target: Any, reset_by_admin: bool) -> None:
    """记录改密。绝不写入任何口令内容——只留"谁在什么时候改了谁的密码"。"""
    scope = "重置用户密码" if reset_by_admin else "修改密码"
    await record_committed_audit(
        "password_change",
        lambda: audit_service.log_user_action(
            action=AuditAction.PASSWORD_CHANGE,
            operator_username=operator.username,
            target_user_id=target.id,
            target_username=target.username,
            operator_id=operator.user_id,
            ip_address=client_ip(request),
            new_value={"reset_by_admin": reset_by_admin},
            description=f"{scope}: {target.username}",
        ),
    )


__all__ = [
    "audit_password_changed",
    "audit_project_updated",
    "audit_task_created",
    "audit_task_deleted",
    "audit_task_executed",
    "audit_task_stopped",
    "audit_task_updated",
    "audit_worker_updated",
]
