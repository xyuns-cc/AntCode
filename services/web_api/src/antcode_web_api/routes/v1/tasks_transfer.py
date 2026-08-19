"""任务导入 / 导出 / 复制 / 模板 / 依赖 / Cron 校验接口。

P2 拆分自 tasks.py 的 8 个 handler + 5 helper + 3 schema + 1 常量：validate-cron、
templates、templates/{id}/create、{id}/export、import、{id}/dependencies(GET/PUT)、
{id}/duplicate。契约 (URL / DI / 返回) 与旧实现一致；create_task_response 等 6 个
helper 由 register_transfer_routes 注入以避免循环 import。
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import Project, Task
from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.task import TaskCreateRequest as TaskCreate
from antcode_core.domain.schemas.task import TaskResponse
from fastapi import Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from antcode_web_api.response import Messages, TaskResponseBuilder
from antcode_web_api.response import success as success_response
from antcode_web_api.routes.v1.mutation_audit import AuditedResource, audit_data_exported, audit_data_imported
from antcode_web_api.utils.yaml_export import yaml_dump as _yaml_dump


class TaskDuplicateRequest(BaseModel):
    name: str | None = None


class TaskDependencyUpdateRequest(BaseModel):
    dependency_ids: list[str] = Field(default_factory=list)


class CronValidateRequest(BaseModel):
    expression: str


TASK_TEMPLATES: list[dict[str, Any]] = []


async def validate_cron_expression(request: CronValidateRequest, current_user):
    """验证 Cron 表达式"""
    _ = current_user
    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(request.expression)
        now = datetime.now(UTC)
        next_runs = []
        last = None
        for _ in range(5):
            next_time = trigger.get_next_fire_time(last, now)
            if not next_time:
                break
            next_runs.append(next_time.isoformat())
            last = next_time

        return success_response({"valid": True, "next_runs": next_runs}, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        return success_response({"valid": False, "error": str(e)}, message=Messages.QUERY_SUCCESS)


async def list_task_templates(current_user):
    """获取任务模板列表"""
    _ = current_user
    return success_response({"templates": TASK_TEMPLATES}, message=Messages.QUERY_SUCCESS)


async def create_task_from_template(
    template_id: str,
    request: dict,
    current_user,
    *,
    create_task_response,
    ensure_specified_worker_access,
):
    """从模板创建任务"""
    template = next((t for t in TASK_TEMPLATES if t.get("id") == template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    merged = {**template.get("payload", {}), **(request or {})}
    task_data = TaskCreate(**merged)

    if not await relation_service.validate_project_user(task_data.project_id, current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or access denied")

    project_info = await relation_service.get_project_with_details(task_data.project_id)
    if not project_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    project = project_info["project"]

    await ensure_specified_worker_access(task_data, current_user)
    task = await scheduler_service.create_task(
        task_data=task_data,
        project_type=ProjectType(project.type),
        user_id=current_user.user_id,
        internal_project_id=project.id,
        specified_worker_id=task_data.specified_worker_id,
    )

    return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)


async def export_task_config(
    task_id: str,
    format: str,
    current_user,
    *,
    http_request,
    task_export_payload,
):
    """导出任务配置"""
    task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    project = await Project.get_or_none(id=task.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    exported = AuditedResource(resource_type="task", resource_name=task.name, resource_id=task.public_id)
    await audit_data_exported(http_request, current_user, exported, scope={"format": format, "project": project.name})

    payload = {
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "task": await task_export_payload(task, project),
    }

    if format == "yaml":
        content = _yaml_dump(payload)
        media_type = "text/yaml"
        filename = f"task_{task.public_id}.yaml"
    else:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        media_type = "application/json"
        filename = f"task_{task.public_id}.json"

    buffer = io.BytesIO(content.encode("utf-8"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type=media_type, headers=headers)


async def _read_task_import(
    file: UploadFile,
    *,
    max_import_bytes: int,
    parse_task_import_payload,
    decode_task_import_bytes,
) -> dict[str, Any]:
    raw_bytes = await file.read(max_import_bytes + 1)
    if len(raw_bytes) > max_import_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"导入文件超过上限 {max_import_bytes // 1024} KiB",
        )
    payload = parse_task_import_payload(decode_task_import_bytes(raw_bytes))
    nested_task = payload.get("task")
    return nested_task.copy() if isinstance(nested_task, dict) else payload.copy()


async def import_task_config(
    file: UploadFile,
    project_id: str | None,
    current_user,
    *,
    http_request,
    max_import_bytes: int,
    create_task_response,
    ensure_specified_worker_access,
    generate_unique_task_name,
    parse_task_import_payload,
    decode_task_import_bytes,
):
    """导入任务配置"""
    task_payload = await _read_task_import(
        file,
        max_import_bytes=max_import_bytes,
        parse_task_import_payload=parse_task_import_payload,
        decode_task_import_bytes=decode_task_import_bytes,
    )
    if project_id:
        task_payload["project_id"] = project_id
    task_project_id = task_payload.get("project_id")
    if not task_project_id:
        raise HTTPException(status_code=400, detail="必须提供 project_id")
    if not await relation_service.validate_project_user(task_project_id, current_user.user_id):
        raise HTTPException(status_code=404, detail="Project not found or access denied")
    project_info = await relation_service.get_project_with_details(task_project_id)
    if not project_info:
        raise HTTPException(status_code=404, detail="Project not found")
    base_name = task_payload.get("name") or "imported-task"
    task_payload["name"] = await generate_unique_task_name(base_name)
    try:
        task_data = TaskCreate(**task_payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"任务配置无效: {exc}") from exc
    project = project_info["project"]
    await ensure_specified_worker_access(task_data, current_user)
    task = await scheduler_service.create_task(
        task_data=task_data,
        project_type=ProjectType(project.type),
        user_id=current_user.user_id,
        internal_project_id=project.id,
        specified_worker_id=getattr(task_data, "specified_worker_id", None),
    )
    imported = AuditedResource(resource_type="task", resource_name=task.name, resource_id=task.public_id)
    scope = {"source_filename": file.filename, "project": project.name}
    await audit_data_imported(http_request, current_user, imported, scope=scope)
    return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)


async def get_task_dependencies(task_id: str, current_user):
    """获取任务依赖关系"""
    from antcode_core.application.services.users.user_service import user_service

    task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    params = task.execution_params if isinstance(task.execution_params, dict) else {}
    dependency_ids = params.get("dependency_ids") if isinstance(params.get("dependency_ids"), list) else []

    is_admin = await user_service.is_admin(current_user.user_id)
    dep_query = Task.filter(public_id__in=dependency_ids)
    if not is_admin:
        dep_query = dep_query.filter(user_id=current_user.user_id)
    dependencies = await dep_query.all()

    candidate_query = Task.all() if is_admin else Task.filter(user_id=current_user.user_id)
    candidates = await candidate_query.all()
    dependents = []
    for candidate in candidates:
        candidate_params = candidate.execution_params if isinstance(candidate.execution_params, dict) else {}
        raw_candidate_deps = candidate_params.get("dependency_ids")
        candidate_deps = raw_candidate_deps if isinstance(raw_candidate_deps, list) else []
        if task.public_id in candidate_deps:
            dependents.append(candidate)

    return success_response(
        {
            "dependencies": TaskResponseBuilder.build_list(dependencies),
            "dependents": TaskResponseBuilder.build_list(dependents),
        },
        message=Messages.QUERY_SUCCESS,
    )


async def update_task_dependencies(
    task_id: str,
    request: TaskDependencyUpdateRequest,
    current_user,
):
    """更新任务依赖关系。

    P2 §4.4: 此前只存 JSON，不做任何校验 —— 不存在的依赖、自依赖和环
    都能写入。现在写入前校验存在性（限本人任务，admin 放宽）、拒绝
    自依赖并做环检测。
    """
    task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    dependency_ids = [str(dep) for dep in (request.dependency_ids or [])]
    await _validate_task_dependencies(task, dependency_ids, current_user.user_id)

    params = task.execution_params if isinstance(task.execution_params, dict) else {}
    params["dependency_ids"] = dependency_ids
    task.execution_params = params
    await task.save()

    return success_response(
        {"dependency_ids": dependency_ids},
        message=Messages.UPDATED_SUCCESS,
    )


async def _validate_task_dependencies(task, dependency_ids: list[str], user_id: int) -> None:
    if not dependency_ids:
        return
    if len(set(dependency_ids)) != len(dependency_ids):
        raise HTTPException(status_code=400, detail="dependency_ids 存在重复项")
    if task.public_id in dependency_ids:
        raise HTTPException(status_code=400, detail="任务不能依赖自身")
    from antcode_core.application.services.users.user_service import user_service

    is_admin = await user_service.is_admin(user_id)
    dep_query = Task.filter(public_id__in=dependency_ids)
    if not is_admin:
        dep_query = dep_query.filter(user_id=user_id)
    found = {dep.public_id for dep in await dep_query.only("id", "public_id").all()}
    missing = sorted(set(dependency_ids) - found)
    if missing:
        raise HTTPException(status_code=400, detail=f"依赖任务不存在或无权访问: {', '.join(missing)}")
    await _reject_dependency_cycle(task, dependency_ids, is_admin=is_admin, user_id=user_id)


async def _reject_dependency_cycle(task, dependency_ids: list[str], *, is_admin: bool, user_id: int) -> None:
    """DFS 检测：从新依赖出发若能回到本任务即成环。"""
    scope_query = Task.all() if is_admin else Task.filter(user_id=user_id)
    graph: dict[str, list[str]] = {}
    for candidate in await scope_query.only("id", "public_id", "execution_params").all():
        params = candidate.execution_params if isinstance(candidate.execution_params, dict) else {}
        deps = params.get("dependency_ids")
        graph[candidate.public_id] = [str(d) for d in deps] if isinstance(deps, list) else []
    graph[task.public_id] = list(dependency_ids)
    stack = list(dependency_ids)
    visited: set[str] = set()
    while stack:
        node = stack.pop()
        if node == task.public_id:
            raise HTTPException(status_code=400, detail="依赖关系存在环，拒绝保存")
        if node in visited:
            continue
        visited.add(node)
        stack.extend(graph.get(node, []))


async def _duplicate_worker_id(worker_id: int | None) -> str | None:
    if not worker_id:
        return None
    from antcode_core.domain.models import Worker

    worker = await Worker.get_or_none(id=worker_id)
    return worker.public_id if worker else None


def _duplicate_task_data(
    task: Task,
    project: Project,
    *,
    name: str,
    worker_id: str | None,
) -> TaskCreate:
    return TaskCreate(
        name=name,
        description=task.description,
        project_id=project.public_id,
        schedule_type=task.schedule_type,
        is_active=task.is_active,
        cron_expression=task.cron_expression,
        interval_seconds=task.interval_seconds,
        scheduled_time=task.scheduled_time,
        max_instances=task.max_instances,
        timeout_seconds=task.timeout_seconds,
        retry_count=task.retry_count,
        retry_delay=task.retry_delay,
        execution_params=task.execution_params,
        environment_vars=task.environment_vars,
        execution_strategy=task.execution_strategy,
        specified_worker_id=worker_id,
    )


async def duplicate_task(
    task_id: str,
    request: TaskDuplicateRequest,
    current_user,
    *,
    create_task_response,
    ensure_specified_worker_access,
    generate_unique_task_name,
):
    """复制任务"""
    from loguru import logger

    try:
        task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        project = await Project.get_or_none(id=task.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        base_name = request.name or f"{task.name}-copy"
        name = await generate_unique_task_name(base_name)
        specified_worker_id = await _duplicate_worker_id(task.specified_worker_id)
        task_data = _duplicate_task_data(task, project, name=name, worker_id=specified_worker_id)
        await ensure_specified_worker_access(task_data, current_user)
        new_task = await scheduler_service.create_task(
            task_data=task_data,
            project_type=ProjectType(project.type),
            user_id=current_user.user_id,
            internal_project_id=project.id,
            specified_worker_id=specified_worker_id,
        )

        return success_response(create_task_response(new_task), message=Messages.CREATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"复制任务失败: {e}")
        raise HTTPException(status_code=500, detail="复制任务失败")


def register_transfer_routes(
    router,
    *,
    max_import_bytes: int,
    create_task_response,
    ensure_specified_worker_access,
    generate_unique_task_name,
    task_export_payload,
    parse_task_import_payload,
    decode_task_import_bytes,
) -> None:
    """挂载 8 个 handler。所有 tasks.py 顶层 helper 通过 kw-only 注入避免循环 import。"""

    @router.post("/validate-cron", response_model=BaseResponse[dict])
    async def _validate_cron_expression(
        request: CronValidateRequest,
        current_user=Depends(get_current_user),
    ):
        """验证 Cron 表达式"""
        return await validate_cron_expression(request, current_user)

    @router.get("/templates", response_model=BaseResponse[dict])
    async def _list_task_templates(current_user=Depends(get_current_user)):
        """获取任务模板列表"""
        return await list_task_templates(current_user)

    @router.post("/templates/{template_id}/create", response_model=BaseResponse[TaskResponse])
    async def _create_task_from_template(
        template_id: str,
        request: dict,
        current_user=Depends(get_current_user),
    ):
        """从模板创建任务"""
        return await create_task_from_template(
            template_id,
            request,
            current_user,
            create_task_response=create_task_response,
            ensure_specified_worker_access=ensure_specified_worker_access,
        )

    @router.get("/{task_id}/export", response_model=None)
    async def _export_task_config(
        task_id: str,
        format: str = Query("json", pattern="^(json|yaml)$"),
        current_user=Depends(get_current_user),
        *,
        http_request: Request,
    ):
        """导出任务配置"""
        return await export_task_config(
            task_id, format, current_user, http_request=http_request, task_export_payload=task_export_payload
        )

    @router.post("/import", response_model=BaseResponse[TaskResponse])
    async def _import_task_config(
        file: UploadFile = File(...),
        project_id: str | None = Form(None),
        current_user=Depends(get_current_user),
        *,
        http_request: Request,
    ):
        """导入任务配置"""
        return await import_task_config(
            file,
            project_id,
            current_user,
            http_request=http_request,
            max_import_bytes=max_import_bytes,
            create_task_response=create_task_response,
            ensure_specified_worker_access=ensure_specified_worker_access,
            generate_unique_task_name=generate_unique_task_name,
            parse_task_import_payload=parse_task_import_payload,
            decode_task_import_bytes=decode_task_import_bytes,
        )

    @router.get("/{task_id}/dependencies", response_model=BaseResponse[dict])
    async def _get_task_dependencies(task_id: str, current_user=Depends(get_current_user)):
        """获取任务依赖关系"""
        return await get_task_dependencies(task_id, current_user)

    @router.put("/{task_id}/dependencies", response_model=BaseResponse[dict])
    async def _update_task_dependencies(
        task_id: str,
        request: TaskDependencyUpdateRequest,
        current_user=Depends(get_current_user),
    ):
        """更新任务依赖关系。

        P2 §4.4: 此前只存 JSON，不做任何校验 —— 不存在的依赖、自依赖和环
        都能写入。现在写入前校验存在性（限本人任务，admin 放宽）、拒绝
        自依赖并做环检测。
        """
        return await update_task_dependencies(task_id, request, current_user)

    @router.post("/{task_id}/duplicate", response_model=BaseResponse[TaskResponse])
    async def _duplicate_task(
        task_id: str,
        request: TaskDuplicateRequest,
        current_user=Depends(get_current_user),
    ):
        """复制任务"""
        return await duplicate_task(
            task_id,
            request,
            current_user,
            create_task_response=create_task_response,
            ensure_specified_worker_access=ensure_specified_worker_access,
            generate_unique_task_name=generate_unique_task_name,
        )


__all__ = [
    "CronValidateRequest",
    "TASK_TEMPLATES",
    "TaskDependencyUpdateRequest",
    "TaskDuplicateRequest",
    "create_task_from_template",
    "duplicate_task",
    "export_task_config",
    "get_task_dependencies",
    "import_task_config",
    "list_task_templates",
    "register_transfer_routes",
    "update_task_dependencies",
    "validate_cron_expression",
]
