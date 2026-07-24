"""任务接口"""

import io
import json
import sys as _sys
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import Project, Task, TaskRun
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
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from tortoise.exceptions import IntegrityError

from antcode_web_api.response import (
    ExecutionResponseBuilder,
    Messages,
    TaskResponseBuilder,
)
from antcode_web_api.response import (
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)

# P2 拆分: /runs/{run_id}/stop, /runs/{run_id}/logs, /runs/{run_id}/logs/download
# 3 个 handler + 4 个 helper 移至 tasks_runs.py; 通过 register_runs_routes 挂路由,
# 顶层 shim 保留 tasks._get_stoppable_execution / stop_task_execution 等测试引用。
# 同时 re-export task_cancel 的 4 个符号让 tests monkeypatch tasks.is_unassigned_task_run
# 等继续生效 (stop_task_execution 通过 tasks_module lookup)。
from antcode_web_api.routes.v1 import tasks_runs as _tasks_runs
from antcode_web_api.routes.v1.runtime_access import ensure_worker_use_access
from antcode_web_api.routes.v1.task_cancel import (  # noqa: F401
    cancel_latest_task_run,
    is_unassigned_task_run,
    mark_task_run_cancelled,
    stop_unassigned_task_run,
)
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids

# P2 §4.4 / 复审 P3: YAML 导出统一走共享工具（保留标量/数字串必须引号）。
from antcode_web_api.utils.yaml_export import yaml_dump as _yaml_dump

tasks_router = APIRouter()
RUNNING_TASK_HARD_CAP = 200
MAX_TASK_IMPORT_BYTES = 1024 * 1024


def create_task_response(task) -> TaskResponse:
    """构建任务响应"""
    return TaskResponseBuilder.build_detail(task)


class TaskExecuteRequest(BaseModel):
    execution_config: dict[str, Any] | None = None
    environment_variables: dict[str, str] | None = None


class TaskBatchRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list, max_length=100)
    action: str = Field(..., description="start/stop/cancel/delete/enable/disable")
    execution_config: dict[str, Any] | None = None

    @field_validator("task_ids")
    @classmethod
    def reject_duplicate_task_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("task_ids 不允许重复")
        return value


class TaskToggleRequest(BaseModel):
    enabled: bool


class TaskDuplicateRequest(BaseModel):
    name: str | None = None


class TaskDependencyUpdateRequest(BaseModel):
    dependency_ids: list[str] = Field(default_factory=list)


class CronValidateRequest(BaseModel):
    expression: str


TASK_TEMPLATES: list[dict[str, Any]] = []


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
        import yaml  # type: ignore  # noqa: F401

        from antcode_web_api.utils.safe_yaml import load_untrusted_yaml

        data = load_untrusted_yaml(raw_text, max_input_bytes=MAX_TASK_IMPORT_BYTES)
        if isinstance(data, dict):
            return data
        raise ValueError("导入内容必须为 YAML 对象")
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="YAML 导入需要安装 PyYAML") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析导入内容失败: {exc}") from exc


def _decode_task_import_bytes(raw_bytes: bytes) -> str:
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="导入文件必须为 UTF-8 编码") from exc


async def _generate_unique_task_name(base_name: str) -> str:
    name = base_name
    idx = 1
    while await Task.filter(name=name).exists():
        name = f"{base_name}-{idx}"
        idx += 1
    return name


async def _task_export_payload(task: Task, project: Project) -> dict[str, Any]:
    """Build a re-importable definition without decrypted runtime secrets."""
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
        "is_active": task.is_active,
        "execution_strategy": task.execution_strategy,
        "specified_worker_id": None,
    }


async def _ensure_specified_worker_access(task_data: TaskCreate | TaskUpdate, current_user) -> None:
    if "specified_worker_id" not in task_data.model_fields_set:
        return
    worker_id = task_data.specified_worker_id
    if worker_id:
        await ensure_worker_use_access(worker_id, current_user.user_id)


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
        await _ensure_specified_worker_access(task_data, current_user)
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
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task name already exists")
    except ValueError as exc:
        logger.info("创建任务配置校验失败: {}", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="任务配置无效") from exc
    except Exception as exc:
        logger.exception("创建任务失败")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="创建任务失败") from exc


@tasks_router.get("", response_model=PaginationResponse[TaskResponse])
async def list_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    is_active: bool | None = None,
    project_id: str | None = Query(None, description="项目ID筛选"),
    schedule_type: str | None = Query(None, description="调度类型筛选"),
    search: str | None = Query(None, description="关键词搜索"),
    specified_worker_id: str | None = Query(None, description="指定执行 Worker ID 筛选"),
    worker_id: str | None = Query(None, description="节点视角 Worker ID 筛选"),
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
        worker_id=worker_id,
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
async def get_running_tasks(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user=Depends(get_current_user),
):
    """获取运行中的任务（带分页）

    P2-15: 之前调用的 `scheduler_service.get_running_tasks()` 并不存在，
    命中即 500。这里改成直接查 TaskRun：取 DISPATCHING / QUEUED /
    RUNNING 三种"正在进行中"的执行记录，非管理员按 Task.user_id
    做归属过滤，最多返回 200 条防止响应体爆炸。
    """
    running_statuses = [
        TaskStatus.DISPATCHING.value,
        TaskStatus.QUEUED.value,
        TaskStatus.RUNNING.value,
    ]
    allowed_task_ids = await _running_task_scope(current_user.user_id, current_user.is_admin)
    if allowed_task_ids == []:
        return success_response([], message=Messages.QUERY_SUCCESS)
    run_query = TaskRun.filter(status__in=running_statuses)
    if allowed_task_ids is not None:
        run_query = run_query.filter(task_id__in=allowed_task_ids)
    runs = await run_query.order_by("-created_at").limit(RUNNING_TASK_HARD_CAP)
    tasks_by_id = await _running_task_map(runs)
    items = [_running_task_item(run, tasks_by_id.get(run.task_id)) for run in runs]
    paginated = items[offset : offset + limit]
    return success_response(paginated, message=Messages.QUERY_SUCCESS)


async def _running_task_scope(user_id: int, is_admin: bool) -> list[int] | None:
    if is_admin:
        return None
    rows = await Task.filter(user_id=user_id).values("id")
    return [int(row["id"]) for row in rows]


async def _running_task_map(runs: list[TaskRun]) -> dict[int, Task]:
    task_ids = list({run.task_id for run in runs})
    if not task_ids:
        return {}
    return {task.id: task for task in await Task.filter(id__in=task_ids).only("id", "public_id", "name")}


def _running_task_item(run: TaskRun, task: Task | None) -> dict[str, Any]:
    return {
        "task_id": task.public_id if task else None,
        "task_name": task.name if task else None,
        "run_id": run.run_id,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "worker_id": run.worker_id,
        "retry_count": run.retry_count,
    }


@tasks_router.get("/stats", response_model=BaseResponse[dict])
async def get_tasks_stats(
    project_id: str | None = Query(None),
    current_user=Depends(get_current_user),
):
    """获取任务统计信息（全局/按项目）"""
    task_query = await _task_stats_query(current_user.user_id, project_id)
    counts = await _task_status_counts(task_query)
    scheduled_tasks = await task_query.filter(
        schedule_type__in=[ScheduleType.CRON, ScheduleType.INTERVAL, ScheduleType.DATE]
    ).count()
    run_stats = await _task_run_stats(task_query)
    data = _task_stats_payload(counts=counts, scheduled_tasks=scheduled_tasks, run_stats=run_stats)
    return success_response(data, message=Messages.QUERY_SUCCESS)


async def _task_stats_query(user_id: int, project_id: str | None) -> Any:
    from antcode_core.application.services.base import QueryHelper
    from antcode_core.application.services.users.user_service import user_service

    user = await user_service.get_user_by_id(user_id)
    is_admin = bool(user and user.is_admin)
    task_query = Task.all() if is_admin else Task.filter(user_id=user_id)
    if not project_id:
        return task_query
    project = await QueryHelper.get_by_id_or_public_id(
        Project,
        project_id,
        user_id=None if is_admin else user_id,
        check_admin=True,
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在或无权限访问")
    return task_query.filter(project_id=project.id)


async def _task_status_counts(task_query: Any) -> dict[str, int]:
    import asyncio

    values = await asyncio.gather(
        task_query.count(),
        task_query.filter(status__in=[TaskStatus.PENDING, TaskStatus.DISPATCHING, TaskStatus.QUEUED]).count(),
        task_query.filter(status=TaskStatus.RUNNING).count(),
        task_query.filter(status=TaskStatus.SUCCESS).count(),
        task_query.filter(status__in=[TaskStatus.FAILED, TaskStatus.TIMEOUT]).count(),
        task_query.filter(status=TaskStatus.CANCELLED).count(),
    )
    keys = ("total", "pending", "running", "success", "failed", "cancelled")
    return dict(zip(keys, values, strict=True))


async def _task_run_stats(task_query: Any) -> dict[str, Any]:
    from tortoise.functions import Avg

    task_ids = await task_query.values_list("id", flat=True)
    run_query = TaskRun.filter(task_id__in=list(task_ids)) if task_ids else TaskRun.filter(id__in=[])
    runs_total = await run_query.count()
    runs_success = await run_query.filter(status=TaskStatus.SUCCESS).count()
    avg_rows = (
        await run_query.filter(duration_seconds__not_isnull=True).annotate(avg=Avg("duration_seconds")).values("avg")
    )
    recent_runs = await run_query.order_by("-created_at").limit(10)
    await _attach_run_task_ids(recent_runs)
    return {
        "total": runs_total,
        "success": runs_success,
        "average_duration": avg_rows[0]["avg"] if avg_rows else 0,
        "recent": recent_runs,
    }


async def _attach_run_task_ids(recent_runs: list[TaskRun]) -> None:
    if not recent_runs:
        return
    task_ids = list({run.task_id for run in recent_runs})
    task_map = {task.id: task.public_id for task in await Task.filter(id__in=task_ids).only("id", "public_id")}
    for run in recent_runs:
        run.task_public_id = task_map.get(run.task_id)


def _task_stats_payload(*, counts: dict[str, int], scheduled_tasks: int, run_stats: dict[str, Any]) -> dict[str, Any]:
    total_tasks = counts["total"]
    return {
        "total_tasks": total_tasks,
        "pending_tasks": counts["pending"],
        "running_tasks": counts["running"],
        "completed_tasks": counts["success"],
        "failed_tasks": counts["failed"],
        "cancelled_tasks": counts["cancelled"],
        "tasks_by_priority": {
            "low": 0,
            "normal": total_tasks,
            "high": 0,
            "urgent": 0,
        },
        "tasks_by_type": {
            "manual": max(0, total_tasks - scheduled_tasks),
            "scheduled": scheduled_tasks,
            "webhook": 0,
            "api": 0,
        },
        "recent_executions": ExecutionResponseBuilder.build_list(run_stats["recent"]),
        "success_rate": (run_stats["success"] / run_stats["total"]) if run_stats["total"] else 0,
        "average_duration": run_stats["average_duration"] or 0,
    }


@tasks_router.post("/validate-cron", response_model=BaseResponse[dict])
async def validate_cron_expression(
    request: CronValidateRequest,
    current_user=Depends(get_current_user),
):
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


@tasks_router.get("/templates", response_model=BaseResponse[dict])
async def list_task_templates(current_user=Depends(get_current_user)):
    """获取任务模板列表"""
    _ = current_user
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

    await _ensure_specified_worker_access(task_data, current_user)
    task = await scheduler_service.create_task(
        task_data=task_data,
        project_type=ProjectType(project.type),
        user_id=current_user.user_id,
        internal_project_id=project.id,
        specified_worker_id=task_data.specified_worker_id,
    )

    return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)


@tasks_router.get("/{task_id}/export", response_model=None)
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
        "exported_at": datetime.now(UTC).isoformat(),
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
    task_payload = await _read_task_import(file)
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
    await _ensure_specified_worker_access(task_data, current_user)
    task = await scheduler_service.create_task(
        task_data=task_data,
        project_type=ProjectType(project.type),
        user_id=current_user.user_id,
        internal_project_id=project.id,
        specified_worker_id=getattr(task_data, "specified_worker_id", None),
    )
    return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)


async def _read_task_import(file: UploadFile) -> dict[str, Any]:
    raw_bytes = await file.read(MAX_TASK_IMPORT_BYTES + 1)
    if len(raw_bytes) > MAX_TASK_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"导入文件超过上限 {MAX_TASK_IMPORT_BYTES // 1024} KiB",
        )
    payload = _parse_task_import_payload(_decode_task_import_bytes(raw_bytes))
    nested_task = payload.get("task")
    return nested_task.copy() if isinstance(nested_task, dict) else payload.copy()


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


@tasks_router.put("/{task_id}/dependencies", response_model=BaseResponse[dict])
async def update_task_dependencies(
    task_id: str,
    request: TaskDependencyUpdateRequest,
    current_user=Depends(get_current_user),
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
        await _ensure_specified_worker_access(task_data, current_user)
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as e:
        logger.error(f"删除任务失败: {e}")
        raise HTTPException(status_code=500, detail="删除任务失败")


@tasks_router.post("/batch-delete", response_model=BaseResponse)
async def batch_delete_tasks(request: dict, current_user=Depends(get_current_user)):
    """批量删除任务"""
    task_ids = bounded_distinct_ids(request.get("task_ids"), "task_ids")

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
        message=f"成功删除 {success_count} 个任务" + (f"，{failed_count} 个失败" if failed_count > 0 else ""),
    )


@tasks_router.post("/batch", response_model=BaseResponse[dict])
async def batch_operate_tasks(request: TaskBatchRequest, current_user=Depends(get_current_user)):
    """批量操作任务"""
    if not request.task_ids:
        raise HTTPException(status_code=400, detail="task_ids不能为空")

    success_ids: list[str] = []
    failed_ids: list[str] = []

    for task_id in request.task_ids:
        try:
            succeeded = await _operate_task(task_id, request.action, current_user.user_id)
            if succeeded:
                success_ids.append(task_id)
            else:
                failed_ids.append(task_id)
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


async def _operate_task(task_id: str, action: str, user_id: int) -> bool:
    if action == "delete":
        return bool(await scheduler_service.delete_task(task_id, user_id))
    if action == "enable":
        return bool(await scheduler_service.update_task(task_id, TaskUpdate(is_active=True), user_id))
    if action == "disable":
        return bool(await scheduler_service.update_task(task_id, TaskUpdate(is_active=False), user_id))
    if action == "start":
        return bool(await scheduler_service.trigger_task_by_user(task_id, user_id))
    if action == "stop":
        return bool(await scheduler_service.pause_task_by_user(task_id, user_id))
    if action == "cancel":
        return await cancel_latest_task_run(task_id, user_id)
    raise HTTPException(status_code=400, detail="不支持的操作类型")


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


async def _acquire_trigger_dedup_lock(task_id: str, user_id: int) -> bool:
    """5s 内禁止同一用户重复触发同一任务

    返回 True 表示获得锁；False 表示 5 秒内已经触发过。
    Redis 不可用时显式失败，避免重复触发绕过去重约束。
    """
    try:
        from antcode_core.infrastructure.redis import get_redis_client

        redis = await get_redis_client()
        lock_key = f"trigger_dedup:{task_id}:{user_id}"
        acquired = await redis.set(lock_key, "1", nx=True, ex=5)
        return bool(acquired)
    except Exception as exc:
        logger.exception("触发去重锁获取失败")
        raise HTTPException(status_code=503, detail="任务触发去重服务不可用") from exc


# 触发后等待新 run 落库的轮询参数：Master 经事件/调度异步建 run。
_NEW_RUN_POLL_ATTEMPTS = 10
_NEW_RUN_POLL_INTERVAL_SECONDS = 0.2


async def _latest_run_pk(task_id_or_public: str, user_id: int) -> tuple[int | None, int | None]:
    """返回 (task内部id, 触发前最新 run 主键)；任务不可见时 (None, None)。"""
    task = await scheduler_service.get_task_by_id(task_id_or_public, user_id)
    if not task:
        return None, None
    latest = await TaskRun.filter(task_id=task.id).order_by("-id").only("id").first()
    return task.id, (latest.id if latest else 0)


async def _resolve_new_run_id(internal_task_id: int | None, baseline_pk: int | None) -> str | None:
    """P2 §4.4: 只返回触发**之后**新建的 run_id。

    此前直接查 latest，Master 尚未建新 run 时会把上一次执行的 run_id
    返回给前端订阅。现在以触发前主键为基线短暂轮询；窗口内没有新 run
    则返回 None（前端可稍后从任务详情拿）。
    """
    if internal_task_id is None or baseline_pk is None:
        return None
    import asyncio as _asyncio

    for _ in range(_NEW_RUN_POLL_ATTEMPTS):
        latest = await TaskRun.filter(task_id=internal_task_id).order_by("-id").only("id", "run_id").first()
        if latest and latest.id > baseline_pk:
            return latest.run_id
        await _asyncio.sleep(_NEW_RUN_POLL_INTERVAL_SECONDS)
    return None


@tasks_router.post("/{task_id}/trigger", response_model=BaseResponse[dict])
async def trigger_task(task_id, current_user=Depends(get_current_user)):
    """立即触发任务

    - T15: Redis 锁去重，5s 内同 task_id+user 只允许触发一次
    - S6: 返回最新一次执行的 run_id，便于前端立即订阅日志
    """
    try:
        if not await _acquire_trigger_dedup_lock(str(task_id), current_user.user_id):
            raise HTTPException(status_code=409, detail="请勿连续触发同一任务")

        internal_task_id, baseline_pk = await _latest_run_pk(str(task_id), current_user.user_id)
        triggered = await scheduler_service.trigger_task_by_user(task_id, current_user.user_id)
        if not triggered:
            raise HTTPException(status_code=404, detail="Task not found")

        # 调度器异步创建 TaskRun；只等待并返回触发后新建的 run_id
        run_id = await _resolve_new_run_id(internal_task_id, baseline_pk)

        return success_response(
            {"task_id": str(task_id), "run_id": run_id, "triggered": True},
            message="任务已触发",
        )
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
        # P2 §4.4: execute 覆盖参数此前被静默忽略 —— 用户以为带覆盖执行，
        # 实际按任务原配置跑。单次覆盖执行的全链路（web_api → 事件 →
        # Master 调度 → 派发）尚未支持，显式拒绝而非静默丢弃。
        if request.execution_config or request.environment_variables:
            raise HTTPException(
                status_code=400,
                detail="execute 暂不支持单次覆盖 execution_config/environment_variables；请先更新任务配置后触发",
            )
        if not await _acquire_trigger_dedup_lock(str(task_id), current_user.user_id):
            raise HTTPException(status_code=409, detail="请勿连续触发同一任务")

        internal_task_id, baseline_pk = await _latest_run_pk(str(task_id), current_user.user_id)
        triggered = await scheduler_service.trigger_task_by_user(task_id, current_user.user_id)
        if not triggered:
            raise HTTPException(status_code=404, detail="Task not found")

        run_id = await _resolve_new_run_id(internal_task_id, baseline_pk)
        return success_response(
            {"task_id": task_id, "run_id": run_id, "triggered": True},
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
        task = await scheduler_service.update_task(task_id, TaskUpdate(is_active=request.enabled), current_user.user_id)
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
        specified_worker_id = await _duplicate_worker_id(task.specified_worker_id)
        task_data = _duplicate_task_data(task, project, name, specified_worker_id)
        await _ensure_specified_worker_access(task_data, current_user)
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


async def _duplicate_worker_id(worker_id: int | None) -> str | None:
    if not worker_id:
        return None
    from antcode_core.domain.models import Worker

    worker = await Worker.get_or_none(id=worker_id)
    return worker.public_id if worker else None


def _duplicate_task_data(task: Task, project: Project, name: str, worker_id: str | None) -> TaskCreate:
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


@tasks_router.get("/{task_id}/runs", response_model=PaginationResponse[TaskRunResponse])
async def list_task_runs(
    task_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
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


# P2 拆分: stop_task_execution / get_task_execution_logs / download_task_execution_logs
# + 4 helper 移至 tasks_runs.py, 通过 register_runs_routes 挂路由。顶层 shim 保留
# tasks._get_stoppable_execution 等测试引用可继续可命中; stop_task_execution 通过
# tasks_module=sys.modules[__name__] lookup 让 monkeypatch(tasks, ...) 生效。
async def stop_task_execution(run_id: str, current_user=None):
    return await _tasks_runs.stop_task_execution(run_id, current_user, tasks_module=_sys.modules[__name__])


async def get_task_execution_logs(run_id: str, page: int = 1, size: int = 200, current_user=None):
    return await _tasks_runs.get_task_execution_logs(run_id, page=page, size=size, current_user=current_user)


async def download_task_execution_logs(run_id: str, format: str = "txt", current_user=None):
    return await _tasks_runs.download_task_execution_logs(run_id, format, current_user)


async def _get_stoppable_execution(run_id: str, user_id: int):
    return await _tasks_runs._get_stoppable_execution(run_id, user_id)


def _stop_task_response(run_id: str, *, remote_cancelled: bool):
    return _tasks_runs._stop_task_response(run_id, remote_cancelled=remote_cancelled)


async def _try_send_stop_event_with_reason(execution, user_id: int):
    return await _tasks_runs._try_send_stop_event_with_reason(execution, user_id)


async def _raise_if_stop_terminal_conflict(run_id: str, user_id: int) -> None:
    await _tasks_runs._raise_if_stop_terminal_conflict(run_id, user_id)


# P2 拆分: 3 个 /runs/{run_id}/... handler 挂路由; 传入本模块让 handler 通过
# tasks_module 引用 monkeypatch 目标符号。
_tasks_runs.register_runs_routes(tasks_router, _sys.modules[__name__])


# 标准导出
router = tasks_router

__all__ = ["tasks_router", "router"]
