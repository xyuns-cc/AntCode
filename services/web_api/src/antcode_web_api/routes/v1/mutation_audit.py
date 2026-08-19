"""Audit mappings for mutations whose ``AuditAction`` existed but was never emitted.

与 ``committed_resource_audit`` 的差别只有取操作者身份的方式：这里的调用点都已
持有 ``TokenData``（含 ``username``），直接用它即可，不再为写一条审计而多查一次
users 表——审计不该给主路径加额外往返。写入失败仍走 ``record_committed_audit``：
计数 + CRITICAL 日志，绝不静默吞掉，也绝不把审计故障伪装成业务失败。
"""

from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.audit import audit_service
from antcode_core.domain.models.audit_log import AuditAction

from antcode_web_api.committed_audit import client_ip, record_committed_audit


@dataclass(frozen=True)
class AuditedResource:
    """导出/导入操作的资源标识。

    导出入口分散在项目、爬取批次、任务三处，资源类型各不相同；收敛成一个值对象，
    调用点就不必各自拼一套 ``resource_*`` 参数，也不必依赖三个模型的属性名一致。
    """

    resource_type: str
    resource_name: str
    resource_id: str | None = None


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


async def audit_data_exported(request: Any, operator: Any, resource: AuditedResource, *, scope: dict[str, Any]) -> None:
    """记录数据导出。

    导出恰恰因为是纯读操作才必须留痕：它不改动任何状态，所以除这条审计之外，系统里
    没有第二处证据能回答"谁在何时把哪批数据带走了"。写操作还能靠数据本身反推，导出
    不能。``scope`` 只记导出范围（格式、是否含日志等），绝不写入被导出的内容本身。
    """
    await record_committed_audit(
        "export_data",
        lambda: audit_service.log(
            action=AuditAction.EXPORT_DATA,
            resource_type=resource.resource_type,
            username=operator.username,
            resource_id=resource.resource_id,
            resource_name=resource.resource_name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            new_value=scope,
            description=f"导出数据: {resource.resource_name}",
        ),
    )


async def audit_data_imported(request: Any, operator: Any, resource: AuditedResource, *, scope: dict[str, Any]) -> None:
    """记录数据导入。

    导入是外部内容进入系统的入口（含从 Git 仓库批量建项目这条供应链路径）。
    ``scope`` 只记来源与规模，不写入导入文件的内容本身。
    """
    await record_committed_audit(
        "import_data",
        lambda: audit_service.log(
            action=AuditAction.IMPORT_DATA,
            resource_type=resource.resource_type,
            username=operator.username,
            resource_id=resource.resource_id,
            resource_name=resource.resource_name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            new_value=scope,
            description=f"导入数据: {resource.resource_name}",
        ),
    )


async def audit_worker_resources_updated(
    request: Any,
    operator: Any,
    worker: Any,
    *,
    old_value: dict[str, Any],
    new_value: dict[str, Any],
) -> None:
    """记录 Worker 资源限额调整。

    与 ``WORKER_UPDATE`` 分开记：这条改的是并发数 / 内存 / CPU 时间配额，直接决定
    集群吞吐与单任务爆炸半径，因此必须留下调整前后的完整快照而不只是字段名。
    """
    await record_committed_audit(
        "worker_resource_update",
        lambda: audit_service.log(
            action=AuditAction.WORKER_RESOURCE_UPDATE,
            resource_type="worker",
            username=operator.username,
            resource_id=worker.public_id,
            resource_name=worker.name,
            user_id=operator.user_id,
            ip_address=client_ip(request),
            old_value=old_value,
            new_value=new_value,
            description=f"调整 Worker 资源限额: {worker.name}",
        ),
    )


__all__ = [
    "AuditedResource",
    "audit_data_exported",
    "audit_data_imported",
    "audit_password_changed",
    "audit_project_updated",
    "audit_task_created",
    "audit_task_deleted",
    "audit_task_executed",
    "audit_task_stopped",
    "audit_task_updated",
    "audit_worker_resources_updated",
    "audit_worker_updated",
]
