"""任务接口"""

import io
import json
import sys as _sys
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import Project, Task
from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from antcode_core.domain.schemas.task import (
    TaskCreateRequest as TaskCreate,
)
from antcode_core.domain.schemas.task import (
    TaskResponse,
)
from antcode_core.domain.schemas.task import (
    TaskUpdateRequest as TaskUpdate,
)
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.exceptions import IntegrityError

from antcode_web_api.response import (
    Messages,
    TaskResponseBuilder,
)
from antcode_web_api.response import (
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)

# P2 拆分: batch-delete + batch 2 个 handler + _operate_task helper +
# TaskBatchRequest schema 移至 tasks_batch.py。顶层 re-export schema。
from antcode_web_api.routes.v1 import tasks_batch as _tasks_batch

# P2 拆分: pause/resume/trigger/execute/toggle 5 个 handler + 3 helper +
# 2 schema (TaskExecuteRequest / TaskToggleRequest) 移至 tasks_execute.py。
# 顶层 re-export schema 让测试 import tasks.TaskExecuteRequest 继续可命中。
from antcode_web_api.routes.v1 import tasks_execute as _tasks_execute

# P2 拆分: /running, /stats, /{task_id}/runs, /{task_id}/schedule-history,
# /{task_id}/stats 5 个查询 handler + 6 个 helper 移至 tasks_query.py; 通过
# register_query_routes 挂路由; 顶层 shim 让 tests 直接引用继续可命中。
from antcode_web_api.routes.v1 import tasks_query as _tasks_query

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

# P2 §4.4 / 复审 P3: YAML 导出统一走共享工具（保留标量/数字串必须引号）。
from antcode_web_api.utils.yaml_export import yaml_dump as _yaml_dump

tasks_router = APIRouter()
RUNNING_TASK_HARD_CAP = 200
MAX_TASK_IMPORT_BYTES = 1024 * 1024


def create_task_response(task) -> TaskResponse:
    """构建任务响应"""
    return TaskResponseBuilder.build_detail(task)


# TaskExecuteRequest / TaskToggleRequest 定义已移至 tasks_execute.py; 顶层 re-export
# 保证 tests / 其它模块 import tasks.TaskExecuteRequest 继续可命中。
TaskExecuteRequest = _tasks_execute.TaskExecuteRequest
TaskToggleRequest = _tasks_execute.TaskToggleRequest


# TaskBatchRequest 定义已移至 tasks_batch.py; 顶层 re-export 保证
# tests import tasks.TaskBatchRequest 继续可命中。
TaskBatchRequest = _tasks_batch.TaskBatchRequest


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


# P2 拆分: /running, /stats, /{task_id}/runs, /{task_id}/schedule-history,
# /{task_id}/stats 5 个查询 handler + 6 helper 移至 tasks_query.py, 通过
# register_query_routes 挂路由; 顶层 shim 让 tests 直接引用继续可命中。
async def get_running_tasks(offset: int = 0, limit: int = 100, current_user=None):
    return await _tasks_query.get_running_tasks(
        offset=offset,
        limit=limit,
        current_user=current_user,
        running_task_hard_cap=RUNNING_TASK_HARD_CAP,
    )


async def get_tasks_stats(project_id: str | None = None, current_user=None):
    return await _tasks_query.get_tasks_stats(project_id, current_user)


async def list_task_runs(
    task_id: str,
    *,
    page: int = 1,
    size: int = 20,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    current_user=None,
):
    return await _tasks_query.list_task_runs(
        task_id,
        page=page,
        size=size,
        status=status,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
    )


async def get_task_schedule_history(
    task_id: str,
    *,
    page: int = 1,
    size: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    current_user=None,
):
    return await _tasks_query.get_task_schedule_history(
        task_id,
        page=page,
        size=size,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
    )


async def get_task_stats(task_id, current_user=None):
    return await _tasks_query.get_task_stats(task_id, current_user)


# 内部 helper shim (被其他文件或测试引用时保留可导入)
_running_task_scope = _tasks_query._running_task_scope
_running_task_map = _tasks_query._running_task_map
_running_task_item = _tasks_query._running_task_item
_task_stats_query = _tasks_query._task_stats_query
_task_status_counts = _tasks_query._task_status_counts
_task_run_stats = _tasks_query._task_run_stats
_attach_run_task_ids = _tasks_query._attach_run_task_ids
_task_stats_payload = _tasks_query._task_stats_payload


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


# P2 拆分: batch-delete + batch 2 个 handler + _operate_task helper 移至
# tasks_batch.py, 通过 register_batch_routes 挂路由。顶层 shim 保留原名。
async def batch_delete_tasks(request: dict, current_user=None):
    return await _tasks_batch.batch_delete_tasks(request, current_user)


async def batch_operate_tasks(request, current_user=None):
    return await _tasks_batch.batch_operate_tasks(request, current_user)


_operate_task = _tasks_batch._operate_task


# P2 拆分: pause/resume/trigger/execute/toggle 5 个 handler + 3 helper +
# 2 schema 移至 tasks_execute.py, 通过 register_execute_routes 挂路由。
# 顶层保留 shim 让 tests / 其它模块 import 继续可命中。
async def pause_task(task_id, current_user=None):
    return await _tasks_execute.pause_task(task_id, current_user)


async def resume_task(task_id, current_user=None):
    return await _tasks_execute.resume_task(task_id, current_user)


async def trigger_task(task_id, current_user=None):
    return await _tasks_execute.trigger_task(task_id, current_user)


async def execute_task(task_id: str, request, current_user=None):
    return await _tasks_execute.execute_task(task_id, request, current_user)


async def toggle_task(task_id: str, request, current_user=None):
    return await _tasks_execute.toggle_task(task_id, request, current_user, create_task_response=create_task_response)


# helper / 常量 module-alias 供测试与其它模块继续引用
_acquire_trigger_dedup_lock = _tasks_execute._acquire_trigger_dedup_lock
_latest_run_pk = _tasks_execute._latest_run_pk
_resolve_new_run_id = _tasks_execute._resolve_new_run_id
_NEW_RUN_POLL_ATTEMPTS = _tasks_execute._NEW_RUN_POLL_ATTEMPTS
_NEW_RUN_POLL_INTERVAL_SECONDS = _tasks_execute._NEW_RUN_POLL_INTERVAL_SECONDS


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


# P2 拆分: 2 个 batch handler 挂路由 (batch-delete / batch)
_tasks_batch.register_batch_routes(tasks_router)

# P2 拆分: 5 个执行控制 handler 挂路由 (pause/resume/trigger/execute/toggle)
_tasks_execute.register_execute_routes(tasks_router, create_task_response=create_task_response)

# P2 拆分: 5 个查询 handler 挂路由 (running/stats/{id}/runs/schedule-history/stats)
_tasks_query.register_query_routes(tasks_router, running_task_hard_cap=RUNNING_TASK_HARD_CAP)

# P2 拆分: 3 个 /runs/{run_id}/... handler 挂路由; 传入本模块让 handler 通过
# tasks_module 引用 monkeypatch 目标符号。
_tasks_runs.register_runs_routes(tasks_router, _sys.modules[__name__])


# 标准导出
router = tasks_router

__all__ = ["tasks_router", "router"]
