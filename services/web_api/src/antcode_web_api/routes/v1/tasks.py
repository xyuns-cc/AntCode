"""任务接口"""

import json
import sys as _sys
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
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
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

# P2 拆分: 8 个 handler (validate-cron / templates / templates/create /
# export / import / dependencies GET/PUT / duplicate) + 4 helper + 3 schema
# + 1 常量 移至 tasks_transfer.py。顶层 re-export schema 与 TASK_TEMPLATES。
from antcode_web_api.routes.v1 import tasks_transfer as _tasks_transfer
from antcode_web_api.routes.v1.runtime_access import ensure_worker_use_access
from antcode_web_api.routes.v1.task_cancel import (  # noqa: F401
    cancel_latest_task_run,
    is_unassigned_task_run,
    mark_task_run_cancelled,
    stop_unassigned_task_run,
)

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


# TaskDuplicateRequest / TaskDependencyUpdateRequest / CronValidateRequest / TASK_TEMPLATES
# 定义已移至 tasks_transfer.py; 顶层 re-export 保证测试引用 tasks.CronValidateRequest 等
# 继续可命中。
TaskDuplicateRequest = _tasks_transfer.TaskDuplicateRequest
TaskDependencyUpdateRequest = _tasks_transfer.TaskDependencyUpdateRequest
CronValidateRequest = _tasks_transfer.CronValidateRequest
TASK_TEMPLATES = _tasks_transfer.TASK_TEMPLATES


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


# P2 拆分: 7 个 handler + 3 helper 移至 tasks_transfer.py; 顶层 shim 保留原名
# 让 tests 直接引用 tasks.export_task_config / tasks.get_task_dependencies 等继续可命中。
async def validate_cron_expression(request, current_user=None):
    return await _tasks_transfer.validate_cron_expression(request, current_user)


async def list_task_templates(current_user=None):
    return await _tasks_transfer.list_task_templates(current_user)


async def create_task_from_template(template_id: str, request: dict, current_user=None):
    return await _tasks_transfer.create_task_from_template(
        template_id,
        request,
        current_user,
        create_task_response=create_task_response,
        ensure_specified_worker_access=_ensure_specified_worker_access,
    )


async def export_task_config(task_id: str, format: str = "json", current_user=None):
    return await _tasks_transfer.export_task_config(
        task_id,
        format,
        current_user,
        task_export_payload=_task_export_payload,
    )


async def import_task_config(file, project_id: str | None = None, current_user=None):
    return await _tasks_transfer.import_task_config(
        file,
        project_id,
        current_user,
        max_import_bytes=MAX_TASK_IMPORT_BYTES,
        create_task_response=create_task_response,
        ensure_specified_worker_access=_ensure_specified_worker_access,
        generate_unique_task_name=_generate_unique_task_name,
        parse_task_import_payload=_parse_task_import_payload,
        decode_task_import_bytes=_decode_task_import_bytes,
    )


async def get_task_dependencies(task_id: str, current_user=None):
    return await _tasks_transfer.get_task_dependencies(task_id, current_user)


async def update_task_dependencies(task_id: str, request, current_user=None):
    return await _tasks_transfer.update_task_dependencies(task_id, request, current_user)


# 私有 helper module-alias
async def _read_task_import(file):
    return await _tasks_transfer._read_task_import(
        file,
        max_import_bytes=MAX_TASK_IMPORT_BYTES,
        parse_task_import_payload=_parse_task_import_payload,
        decode_task_import_bytes=_decode_task_import_bytes,
    )


_validate_task_dependencies = _tasks_transfer._validate_task_dependencies
_reject_dependency_cycle = _tasks_transfer._reject_dependency_cycle

# 单段静态路径必须先于 /{task_id} 注册，否则 Starlette 会把 templates、
# running、stats 等字面量当成 task_id。
_tasks_transfer.register_transfer_routes(
    tasks_router,
    max_import_bytes=MAX_TASK_IMPORT_BYTES,
    create_task_response=create_task_response,
    ensure_specified_worker_access=_ensure_specified_worker_access,
    generate_unique_task_name=_generate_unique_task_name,
    task_export_payload=_task_export_payload,
    parse_task_import_payload=_parse_task_import_payload,
    decode_task_import_bytes=_decode_task_import_bytes,
)
_tasks_batch.register_batch_routes(tasks_router)
_tasks_query.register_query_routes(tasks_router, running_task_hard_cap=RUNNING_TASK_HARD_CAP)


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


async def duplicate_task(task_id: str, request, current_user=None):
    return await _tasks_transfer.duplicate_task(
        task_id,
        request,
        current_user,
        create_task_response=create_task_response,
        ensure_specified_worker_access=_ensure_specified_worker_access,
        generate_unique_task_name=_generate_unique_task_name,
    )


_duplicate_worker_id = _tasks_transfer._duplicate_worker_id
_duplicate_task_data = _tasks_transfer._duplicate_task_data


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


# P2 拆分: 5 个执行控制 handler 挂路由 (pause/resume/trigger/execute/toggle)
_tasks_execute.register_execute_routes(tasks_router, create_task_response=create_task_response)

# P2 拆分: 3 个 /runs/{run_id}/... handler 挂路由; 传入本模块让 handler 通过
# tasks_module 引用 monkeypatch 目标符号。
_tasks_runs.register_runs_routes(tasks_router, _sys.modules[__name__])


# 标准导出
router = tasks_router

__all__ = ["tasks_router", "router"]
