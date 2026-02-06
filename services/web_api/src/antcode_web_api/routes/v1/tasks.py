"""任务接口"""

import io
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.exceptions import IntegrityError

from antcode_web_api.response import (
    ExecutionResponseBuilder,
    Messages,
    TaskResponseBuilder,
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models.enums import ProjectType, ScheduleType, TaskStatus
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from antcode_core.domain.schemas.task import (
    TaskCreateRequest as TaskCreate,
)
from antcode_core.domain.schemas.task import (
    TaskResponse,
    TaskRunResponse,
    TaskStatsResponse,
)
from antcode_core.domain.schemas.task import (
    TaskUpdateRequest as TaskUpdate,
)
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.domain.models import Project, Task, TaskRun
from antcode_web_api.utils.simple_yaml import parse_simple_yaml

tasks_router = APIRouter()


def create_task_response(task) -> TaskResponse:
    """构建任务响应"""
    return TaskResponseBuilder.build_detail(task)


class TaskExecuteRequest(BaseModel):
    execution_config: dict[str, Any] | None = None
    environment_variables: dict[str, str] | None = None


class TaskBatchRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    action: str = Field(..., description="start/stop/cancel/delete/enable/disable")
    execution_config: dict[str, Any] | None = None


class TaskToggleRequest(BaseModel):
    enabled: bool


class TaskDuplicateRequest(BaseModel):
    name: str | None = None


class TaskDependencyUpdateRequest(BaseModel):
    dependency_ids: list[str] = Field(default_factory=list)


class CronValidateRequest(BaseModel):
    expression: str


TASK_TEMPLATES: list[dict[str, Any]] = []


def _yaml_dump(data: Any, indent: int = 0) -> str:
    """简单 YAML 序列化（避免额外依赖）"""
    prefix = "  " * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_yaml_dump(value, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_yaml_dump(item, indent + 1))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_yaml_scalar(data)}"


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(c in text for c in [":", "-", "#", "\n", "\"", "'"]):
        return json.dumps(text, ensure_ascii=False)
    return text


def _parse_task_import_payload(raw_text: str) -> dict[str, Any]:
    """解析任务导入内容"""
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            return data
        raise ValueError("导入内容必须为 JSON 对象")
    except json.JSONDecodeError:
        pass

    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw_text)
        if isinstance(data, dict):
            return data
        raise ValueError("导入内容必须为 YAML 对象")
    except ImportError as exc:
        try:
            data = parse_simple_yaml(raw_text)
            if isinstance(data, dict):
                return data
            raise ValueError("导入内容必须为 YAML 对象")
        except Exception as fallback_exc:
            raise HTTPException(
                status_code=400, detail=f"解析导入内容失败: {fallback_exc}"
            ) from fallback_exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析导入内容失败: {exc}") from exc


async def _generate_unique_task_name(base_name: str) -> str:
    name = base_name
    idx = 1
    while await Task.filter(name=name).exists():
        name = f"{base_name}-{idx}"
        idx += 1
    return name


async def _task_export_payload(task: Task, project: Project) -> dict[str, Any]:
    return {
        "name": task.name,
        "description": task.description or "",
        "project_id": project.public_id,
        "schedule_type": task.schedule_type,
        "cron_expression": task.cron_expression,
        "interval_seconds": task.interval_seconds,
        "scheduled_time": task.scheduled_time.isoformat() if task.scheduled_time else None,
        "max_instances": task.max_instances,
        "timeout_seconds": task.timeout_seconds,
        "retry_count": task.retry_count,
        "retry_delay": task.retry_delay,
        "execution_params": task.execution_params or {},
        "environment_vars": task.environment_vars or {},
        "is_active": task.is_active,
        "execution_strategy": task.execution_strategy,
        "specified_worker_id": None,
    }


@tasks_router.post("", response_model=BaseResponse[TaskResponse])
async def create_task(task_data: TaskCreate, current_user=Depends(get_current_user)):
    # 验证项目权限
    if not await relation_service.validate_project_user(task_data.project_id, current_user.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found or access denied",
        )

    # 获取项目信息
    project_info = await relation_service.get_project_with_details(task_data.project_id)
    if not project_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    project = project_info["project"]

    try:
        specified_worker_id = getattr(task_data, "specified_worker_id", None)
        # 使用service层创建任务，传递内部 project_id
        task = await scheduler_service.create_task(
            task_data=task_data,
            project_type=ProjectType(project.type),
            user_id=current_user.user_id,
            internal_project_id=project.id,  # 传递内部 id
            specified_worker_id=specified_worker_id,
        )

        return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task name already exists")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@tasks_router.get("", response_model=PaginationResponse[TaskResponse])
async def list_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str = None,
    is_active: bool = None,
    project_id: str | None = Query(None, description="项目ID筛选"),
    schedule_type: str | None = Query(None, description="调度类型筛选"),
    search: str | None = Query(None, description="关键词搜索"),
    specified_worker_id: str = Query(None, description="指定执行 Worker ID 筛选"),
    current_user=Depends(get_current_user),
):
    """获取任务列表"""
    from antcode_core.application.services.users.user_service import user_service

    is_admin = await user_service.is_admin(current_user.user_id)
    result = await scheduler_service.get_user_tasks(
        user_id=None if is_admin else current_user.user_id,
        status=status,
        is_active=is_active,
        page=page,
        size=size,
        specified_worker_id=specified_worker_id,
        project_id=project_id,
        schedule_type=schedule_type,
        search=search,
    )

    return page_response(
        items=TaskResponseBuilder.build_list(result["tasks"]),
        total=result["total"],
        page=result["page"],
        size=result["size"],
        message=Messages.QUERY_SUCCESS,
    )


@tasks_router.get("/running", response_model=BaseResponse[list])
async def get_running_tasks(current_user=Depends(get_current_user)):
    """获取运行中的任务"""
    from antcode_core.domain.models import Task

    running = scheduler_service.get_running_tasks()

    if not running:
        return success_response([], message=Messages.QUERY_SUCCESS)

    # 批量获取任务 ID，避免 N+1 查询
    task_ids = [info["task_id"] for info in running]

    # 批量查询用户有权限的任务
    if current_user.is_admin:
        tasks = await Task.filter(id__in=task_ids).all()
    else:
        tasks = await Task.filter(id__in=task_ids, user_id=current_user.user_id).all()

    valid_task_ids = {t.id for t in tasks}

    # 过滤只显示当前用户有权限的任务
    user_tasks = [info for info in running if info["task_id"] in valid_task_ids]

    return success_response(user_tasks, message=Messages.QUERY_SUCCESS)


@tasks_router.get("/stats", response_model=BaseResponse[dict])
async def get_tasks_stats(
    project_id: str | None = Query(None),
    current_user=Depends(get_current_user),
):
    """获取任务统计信息（全局/按项目）"""
    import asyncio

    from tortoise.functions import Avg

    from antcode_core.application.services.base import QueryHelper
    from antcode_core.application.services.users.user_service import user_service

    user = await user_service.get_user_by_id(current_user.user_id)
    is_admin = bool(user and user.is_admin)

    task_query = Task.all() if is_admin else Task.filter(user_id=current_user.user_id)

    if project_id:
        project = await QueryHelper.get_by_id_or_public_id(
            Project,
            project_id,
            user_id=None if is_admin else current_user.user_id,
            check_admin=True,
        )
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在或无权限访问")
        task_query = task_query.filter(project_id=project.id)

    (
        total_tasks,
        pending_tasks,
        running_tasks,
        success_tasks,
        failed_tasks,
        cancelled_tasks,
    ) = await asyncio.gather(
        task_query.count(),
        task_query.filter(
            status__in=[TaskStatus.PENDING, TaskStatus.DISPATCHING, TaskStatus.QUEUED]
        ).count(),
        task_query.filter(status=TaskStatus.RUNNING).count(),
        task_query.filter(status=TaskStatus.SUCCESS).count(),
        task_query.filter(status__in=[TaskStatus.FAILED, TaskStatus.TIMEOUT]).count(),
        task_query.filter(status=TaskStatus.CANCELLED).count(),
    )

    scheduled_tasks = await task_query.filter(
        schedule_type__in=[ScheduleType.CRON, ScheduleType.INTERVAL, ScheduleType.DATE]
    ).count()
    manual_tasks = max(0, total_tasks - scheduled_tasks)

    task_ids = await task_query.values_list("id", flat=True)
    run_query = TaskRun.filter(task_id__in=list(task_ids)) if task_ids else TaskRun.filter(id__in=[])

    runs_total = await run_query.count()
    runs_success = await run_query.filter(status=TaskStatus.SUCCESS).count()
    avg_duration_raw = await run_query.filter(duration_seconds__not_isnull=True).annotate(
        avg=Avg("duration_seconds")
    ).values("avg")
    avg_duration = avg_duration_raw[0]["avg"] if avg_duration_raw else 0

    recent_runs = await run_query.order_by("-created_at").limit(10)
    if recent_runs:
        recent_task_ids = list({run.task_id for run in recent_runs})
        task_map = {
            t.id: t.public_id for t in await Task.filter(id__in=recent_task_ids).only("id", "public_id")
        }
        for run in recent_runs:
            run.task_public_id = task_map.get(run.task_id)

    data = {
        "total_tasks": total_tasks,
        "pending_tasks": pending_tasks,
        "running_tasks": running_tasks,
        "completed_tasks": success_tasks,
        "failed_tasks": failed_tasks,
        "cancelled_tasks": cancelled_tasks,
        "tasks_by_priority": {
            "low": 0,
            "normal": total_tasks,
            "high": 0,
            "urgent": 0,
        },
        "tasks_by_type": {
            "manual": manual_tasks,
            "scheduled": scheduled_tasks,
            "webhook": 0,
            "api": 0,
        },
        "recent_executions": ExecutionResponseBuilder.build_list(recent_runs),
        "success_rate": (runs_success / runs_total) if runs_total else 0,
        "average_duration": avg_duration or 0,
    }

    return success_response(data, message=Messages.QUERY_SUCCESS)


@tasks_router.post("/validate-cron", response_model=BaseResponse[dict])
async def validate_cron_expression(request: CronValidateRequest):
    """验证 Cron 表达式"""
    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(request.expression)
        now = datetime.now(timezone.utc)
        next_runs = []
        last = None
        for _ in range(5):
            next_time = trigger.get_next_fire_time(last, now)
            if not next_time:
                break
            next_runs.append(next_time.isoformat())
            last = next_time

        return success_response(
            {"valid": True, "next_runs": next_runs}, message=Messages.QUERY_SUCCESS
        )
    except Exception as e:
        return success_response({"valid": False, "error": str(e)}, message=Messages.QUERY_SUCCESS)


@tasks_router.get("/templates", response_model=BaseResponse[dict])
async def list_task_templates():
    """获取任务模板列表"""
    return success_response({"templates": TASK_TEMPLATES}, message=Messages.QUERY_SUCCESS)


@tasks_router.post("/templates/{template_id}/create", response_model=BaseResponse[TaskResponse])
async def create_task_from_template(
    template_id: str,
    request: dict,
    current_user=Depends(get_current_user),
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

    task = await scheduler_service.create_task(
        task_data=task_data,
        project_type=ProjectType(project.type),
        user_id=current_user.user_id,
        internal_project_id=project.id,
    )

    return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)


@tasks_router.get("/{task_id}/export")
async def export_task_config(
    task_id: str,
    format: str = Query("json", pattern="^(json|yaml)$"),
    current_user=Depends(get_current_user),
):
    """导出任务配置"""
    task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    project = await Project.get_or_none(id=task.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "task": await _task_export_payload(task, project),
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


@tasks_router.post("/import", response_model=BaseResponse[TaskResponse])
async def import_task_config(
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    current_user=Depends(get_current_user),
):
    """导入任务配置"""
    raw_text = (await file.read()).decode("utf-8", errors="ignore")
    payload = _parse_task_import_payload(raw_text)
    task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else payload

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
    task_payload["name"] = await _generate_unique_task_name(base_name)

    try:
        task_data = TaskCreate(**task_payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"任务配置无效: {exc}") from exc

    project = project_info["project"]
    task = await scheduler_service.create_task(
        task_data=task_data,
        project_type=ProjectType(project.type),
        user_id=current_user.user_id,
        internal_project_id=project.id,
        specified_worker_id=getattr(task_data, "specified_worker_id", None),
    )

    return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)


@tasks_router.get("/{task_id}/dependencies", response_model=BaseResponse[dict])
async def get_task_dependencies(task_id: str, current_user=Depends(get_current_user)):
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
        candidate_params = (
            candidate.execution_params
            if isinstance(candidate.execution_params, dict)
            else {}
        )
        candidate_deps = candidate_params.get("dependency_ids") or []
        if task.public_id in candidate_deps:
            dependents.append(candidate)

    return success_response(
        {
            "dependencies": TaskResponseBuilder.build_list(dependencies),
            "dependents": TaskResponseBuilder.build_list(dependents),
        },
        message=Messages.QUERY_SUCCESS,
    )


@tasks_router.put("/{task_id}/dependencies", response_model=BaseResponse[dict])
async def update_task_dependencies(
    task_id: str,
    request: TaskDependencyUpdateRequest,
    current_user=Depends(get_current_user),
):
    """更新任务依赖关系"""
    task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    params = task.execution_params if isinstance(task.execution_params, dict) else {}
    params["dependency_ids"] = request.dependency_ids
    task.execution_params = params
    await task.save()

    return success_response(
        {"dependency_ids": request.dependency_ids},
        message=Messages.UPDATED_SUCCESS,
    )


@tasks_router.get("/{task_id}", response_model=BaseResponse[TaskResponse])
async def get_task(task_id, current_user=Depends(get_current_user)):
    try:
        task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(create_task_response(task), message=Messages.QUERY_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务失败: {e}")
        raise HTTPException(status_code=500, detail="获取任务失败")


@tasks_router.put("/{task_id}", response_model=BaseResponse[TaskResponse])
async def update_task(task_id, task_data: TaskUpdate, current_user=Depends(get_current_user)):
    try:
        task = await scheduler_service.update_task(task_id, task_data, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(create_task_response(task), message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新任务失败: {e}")
        raise HTTPException(status_code=500, detail="更新任务失败")


@tasks_router.delete("/{task_id}", response_model=BaseResponse)
async def delete_task(task_id, current_user=Depends(get_current_user)):
    try:
        deleted = await scheduler_service.delete_task(task_id, current_user.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message=Messages.DELETED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除任务失败: {e}")
        raise HTTPException(status_code=500, detail="删除任务失败")


@tasks_router.post("/batch-delete", response_model=BaseResponse)
async def batch_delete_tasks(request: dict, current_user=Depends(get_current_user)):
    """批量删除任务"""
    task_ids = request.get("task_ids", [])
    if not task_ids:
        raise HTTPException(status_code=400, detail="task_ids不能为空")

    success_count = 0
    failed_count = 0
    failed_ids = []

    for task_id in task_ids:
        try:
            deleted = await scheduler_service.delete_task(task_id, current_user.user_id)
            if deleted:
                success_count += 1
            else:
                failed_count += 1
                failed_ids.append(task_id)
        except Exception as e:
            logger.warning(f"删除任务 {task_id} 失败: {e}")
            failed_count += 1
            failed_ids.append(task_id)

    return success_response(
        {
            "success_count": success_count,
            "failed_count": failed_count,
            "failed_ids": failed_ids,
        },
        message=f"成功删除 {success_count} 个任务"
        + (f"，{failed_count} 个失败" if failed_count > 0 else ""),
    )


@tasks_router.post("/batch", response_model=BaseResponse[dict])
async def batch_operate_tasks(
    request: TaskBatchRequest, current_user=Depends(get_current_user)
):
    """批量操作任务"""
    if not request.task_ids:
        raise HTTPException(status_code=400, detail="task_ids不能为空")

    action = request.action
    success_ids = []
    failed_ids = []

    for task_id in request.task_ids:
        try:
            if action == "delete":
                deleted = await scheduler_service.delete_task(task_id, current_user.user_id)
                if deleted:
                    success_ids.append(task_id)
                else:
                    failed_ids.append(task_id)
            elif action == "enable":
                updated = await scheduler_service.update_task(
                    task_id, TaskUpdate(is_active=True), current_user.user_id
                )
                if updated:
                    success_ids.append(task_id)
                else:
                    failed_ids.append(task_id)
            elif action == "disable":
                updated = await scheduler_service.update_task(
                    task_id, TaskUpdate(is_active=False), current_user.user_id
                )
                if updated:
                    success_ids.append(task_id)
                else:
                    failed_ids.append(task_id)
            elif action == "start":
                triggered = await scheduler_service.trigger_task_by_user(
                    task_id, current_user.user_id
                )
                if triggered:
                    success_ids.append(task_id)
                else:
                    failed_ids.append(task_id)
            elif action == "stop":
                paused = await scheduler_service.pause_task_by_user(
                    task_id, current_user.user_id
                )
                if paused:
                    success_ids.append(task_id)
                else:
                    failed_ids.append(task_id)
            elif action == "cancel":
                # 取消任务最近一次执行
                task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
                if not task:
                    failed_ids.append(task_id)
                    continue
                execution = await TaskRun.filter(
                    task_id=task.id,
                    status__in=[
                        TaskStatus.PENDING,
                        TaskStatus.DISPATCHING,
                        TaskStatus.QUEUED,
                        TaskStatus.RUNNING,
                    ],
                ).order_by("-created_at").first()
                if not execution:
                    failed_ids.append(task_id)
                    continue
                cancelled = False
                if execution.worker_id:
                    try:
                        from antcode_core.application.services.workers.worker_service import (
                            worker_service,
                        )
                        from antcode_core.infrastructure.redis import get_redis_client

                        worker = await worker_service.get_worker_by_id(execution.worker_id)
                        if worker:
                            redis = await get_redis_client()
                            payload = {
                                "control_type": "cancel",
                                "task_id": execution.execution_id,
                                "run_id": execution.execution_id,
                                "reason": f"user_cancel:{current_user.user_id}",
                            }
                            await redis.xadd(f"antcode:control:{worker.public_id}", payload)
                            cancelled = True
                    except Exception as e:
                        logger.warning(f"发送取消指令失败: {e}")

                from antcode_core.application.services.scheduler.execution_status_service import (
                    execution_status_service,
                )

                await execution_status_service.update_runtime_status(
                    execution_id=execution.execution_id,
                    status="cancelled",
                    status_at=datetime.now(timezone.utc),
                    error_message=f"用户取消 (user_id={current_user.user_id})",
                )
                if cancelled:
                    success_ids.append(task_id)
                else:
                    success_ids.append(task_id)
            else:
                raise HTTPException(status_code=400, detail="不支持的操作类型")
        except Exception:
            failed_ids.append(task_id)

    return success_response(
        {
            "success_count": len(success_ids),
            "failed_count": len(failed_ids),
            "success_ids": success_ids,
            "failed_ids": failed_ids,
        },
        message=Messages.OPERATION_SUCCESS,
    )


@tasks_router.post("/{task_id}/pause", response_model=BaseResponse)
async def pause_task(task_id, current_user=Depends(get_current_user)):
    """暂停任务"""
    try:
        paused = await scheduler_service.pause_task_by_user(task_id, current_user.user_id)
        if not paused:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message="任务已暂停")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"暂停任务失败: {e}")
        raise HTTPException(status_code=500, detail="暂停任务失败")


@tasks_router.post("/{task_id}/resume", response_model=BaseResponse)
async def resume_task(task_id, current_user=Depends(get_current_user)):
    """恢复任务"""
    try:
        resumed = await scheduler_service.resume_task_by_user(task_id, current_user.user_id)
        if not resumed:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message="任务已恢复")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复任务失败: {e}")
        raise HTTPException(status_code=500, detail="恢复任务失败")


@tasks_router.post("/{task_id}/trigger", response_model=BaseResponse)
async def trigger_task(task_id, current_user=Depends(get_current_user)):
    """立即触发任务"""
    try:
        triggered = await scheduler_service.trigger_task_by_user(task_id, current_user.user_id)
        if not triggered:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message="任务已触发")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"触发任务失败: {e}")
        raise HTTPException(status_code=500, detail="触发任务失败")


@tasks_router.post("/{task_id}/execute", response_model=BaseResponse[dict])
async def execute_task(
    task_id: str,
    request: TaskExecuteRequest,
    current_user=Depends(get_current_user),
):
    """执行任务（触发立即执行）"""
    try:
        triggered = await scheduler_service.trigger_task_by_user(task_id, current_user.user_id)
        if not triggered:
            raise HTTPException(status_code=404, detail="Task not found")
        return success_response(
            {"task_id": task_id, "triggered": True},
            message="任务已触发",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"执行任务失败: {e}")
        raise HTTPException(status_code=500, detail="执行任务失败")


@tasks_router.patch("/{task_id}/toggle", response_model=BaseResponse[TaskResponse])
async def toggle_task(task_id: str, request: TaskToggleRequest, current_user=Depends(get_current_user)):
    """启用/禁用任务"""
    try:
        task = await scheduler_service.update_task(
            task_id, TaskUpdate(is_active=request.enabled), current_user.user_id
        )
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return success_response(create_task_response(task), message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换任务状态失败: {e}")
        raise HTTPException(status_code=500, detail="切换任务状态失败")


@tasks_router.post("/{task_id}/duplicate", response_model=BaseResponse[TaskResponse])
async def duplicate_task(
    task_id: str,
    request: TaskDuplicateRequest,
    current_user=Depends(get_current_user),
):
    """复制任务"""
    try:
        task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        project = await Project.get_or_none(id=task.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        base_name = request.name or f"{task.name}-copy"
        name = await _generate_unique_task_name(base_name)

        specified_worker_id = None
        if task.specified_worker_id:
            from antcode_core.domain.models import Worker

            worker = await Worker.get_or_none(id=task.specified_worker_id)
            specified_worker_id = worker.public_id if worker else None

        task_data = TaskCreate(
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
            specified_worker_id=specified_worker_id,
        )

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


@tasks_router.get("/{task_id}/runs", response_model=PaginationResponse[TaskRunResponse])
async def list_task_runs(
    task_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str = None,
    start_date: str = None,
    end_date: str = None,
    current_user=Depends(get_current_user),
):
    """获取任务运行历史"""
    try:
        result = await scheduler_service.get_task_executions(
            task_id=task_id,
            user_id=current_user.user_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
            size=size,
        )

        return page_response(
            items=ExecutionResponseBuilder.build_list(result["executions"]),
            total=result["total"],
            page=result["page"],
            size=result["size"],
            message=Messages.QUERY_SUCCESS,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@tasks_router.get("/{task_id}/schedule-history", response_model=PaginationResponse[TaskRunResponse])
async def get_task_schedule_history(
    task_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_user=Depends(get_current_user),
):
    """获取任务调度历史（复用执行记录）"""
    try:
        result = await scheduler_service.get_task_executions(
            task_id=task_id,
            user_id=current_user.user_id,
            status=None,
            start_date=start_date,
            end_date=end_date,
            page=page,
            size=size,
        )

        return page_response(
            items=ExecutionResponseBuilder.build_list(result["executions"]),
            total=result["total"],
            page=result["page"],
            size=result["size"],
            message=Messages.QUERY_SUCCESS,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@tasks_router.get("/{task_id}/stats", response_model=BaseResponse[TaskStatsResponse])
async def get_task_stats(task_id, current_user=Depends(get_current_user)):
    """获取任务统计信息"""
    try:
        stats_data = await scheduler_service.get_task_stats(task_id, current_user.user_id)
        if not stats_data:
            raise HTTPException(status_code=404, detail="Task not found")

        stats = TaskStatsResponse(
            total_executions=stats_data["total_executions"],
            success_count=stats_data["success_count"],
            failed_count=stats_data["failed_count"],
            success_rate=stats_data["success_rate"] / 100,  # 转换为小数
            average_duration=stats_data["avg_duration"],
        )

        return success_response(stats, message=Messages.QUERY_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取任务统计失败")


@tasks_router.post("/executions/{execution_id}/stop", response_model=BaseResponse[dict])
async def stop_task_execution(execution_id: str, current_user=Depends(get_current_user)):
    """停止任务执行"""
    from antcode_core.application.services.scheduler.execution_status_service import (
        execution_status_service,
    )

    execution = await scheduler_service.get_execution_with_permission(
        execution_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    if execution.status not in (
        TaskStatus.PENDING,
        TaskStatus.DISPATCHING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ):
        raise HTTPException(status_code=400, detail="任务当前状态无法停止")

    cancelled = False
    if execution.worker_id:
        try:
            from antcode_core.application.services.workers.worker_service import worker_service
            from antcode_core.infrastructure.redis import get_redis_client

            worker = await worker_service.get_worker_by_id(execution.worker_id)
            if worker:
                redis = await get_redis_client()
                payload = {
                    "control_type": "cancel",
                    "task_id": execution.execution_id,
                    "run_id": execution.execution_id,
                    "reason": f"user_cancel:{current_user.user_id}",
                }
                await redis.xadd(f"antcode:control:{worker.public_id}", payload)
                cancelled = True
        except Exception as e:
            logger.warning(f"发送取消指令失败: {e}")

    await execution_status_service.update_runtime_status(
        execution_id=execution.execution_id,
        status="cancelled",
        status_at=datetime.now(timezone.utc),
        error_message=f"用户取消 (user_id={current_user.user_id})",
    )

    return success_response(
        {"execution_id": execution_id, "status": "cancelled", "remote_cancelled": cancelled},
        message="任务已停止",
    )


@tasks_router.get("/executions/{execution_id}/logs", response_model=PaginationResponse[dict])
async def get_task_execution_logs(
    execution_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(200, ge=1, le=1000),
    current_user=Depends(get_current_user),
):
    """获取任务执行日志（分页）"""
    from antcode_core.application.services.logs.task_log_service import task_log_service

    execution = await scheduler_service.get_execution_with_permission(
        execution_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    logs = await task_log_service.get_execution_logs(execution.execution_id)
    items = []
    for line in (logs.get("output") or "").splitlines():
        if line.strip():
            items.append({"type": "stdout", "message": line})
    for line in (logs.get("error") or "").splitlines():
        if line.strip():
            items.append({"type": "stderr", "message": line})

    total = len(items)
    start = (page - 1) * size
    end = start + size
    return page_response(
        items=items[start:end],
        total=total,
        page=page,
        size=size,
        message=Messages.QUERY_SUCCESS,
    )


@tasks_router.get("/executions/{execution_id}/logs/download")
async def download_task_execution_logs(
    execution_id: str,
    format: str = Query("txt", pattern="^(txt|json)$"),
    current_user=Depends(get_current_user),
):
    """下载任务执行日志"""
    from antcode_core.application.services.logs.task_log_service import task_log_service

    execution = await scheduler_service.get_execution_with_permission(
        execution_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    logs = await task_log_service.get_execution_logs(execution.execution_id)
    if format == "json":
        content = json.dumps(logs, ensure_ascii=False, indent=2)
        media_type = "application/json"
        filename = f"execution_{execution_id}.json"
    else:
        content = "\n".join(
            [
                "=== STDOUT ===",
                logs.get("output", ""),
                "",
                "=== STDERR ===",
                logs.get("error", ""),
            ]
        )
        media_type = "text/plain"
        filename = f"execution_{execution_id}.txt"

    buffer = io.BytesIO(content.encode("utf-8"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type=media_type, headers=headers)


# 标准导出
router = tasks_router

__all__ = ["tasks_router", "router"]
