"""任务查询接口 (统计 / 运行中 / 历史执行 / 单任务统计)。

P2 拆分自 tasks.py: 5 个纯查询 handler:
- GET /tasks/running (get_running_tasks)
- GET /tasks/stats (get_tasks_stats)
- GET /tasks/{task_id}/runs (list_task_runs)
- GET /tasks/{task_id}/schedule-history (get_task_schedule_history)
- GET /tasks/{task_id}/stats (get_task_stats)

契约 (URL / DI / 返回) 与旧实现一致。RUNNING_TASK_HARD_CAP 由
register_query_routes 时注入避免循环 import。
"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import Project, Task, TaskRun
from antcode_core.domain.models.enums import ScheduleType, TaskStatus
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from antcode_core.domain.schemas.task import TaskRunResponse, TaskStatsResponse
from fastapi import Depends, HTTPException, Query

from antcode_web_api.response import (
    ExecutionResponseBuilder,
    Messages,
    TaskResponseBuilder,  # noqa: F401  (kept for parity with tasks.py imports)
)
from antcode_web_api.response import (
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)


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


async def get_running_tasks(
    *,
    offset: int,
    limit: int,
    current_user,
    running_task_hard_cap: int,
):
    """获取运行中的任务（带分页）

    P2-15: 之前调用的 `scheduler_service.get_running_tasks()` 并不存在，
    命中即 500。这里改成直接查 TaskRun：取 DISPATCHING / QUEUED /
    RUNNING 三种"正在进行中"的执行记录，非管理员按 Task.user_id
    做归属过滤，最多返回 running_task_hard_cap 条防止响应体爆炸。
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
    runs = await run_query.order_by("-created_at").limit(running_task_hard_cap)
    tasks_by_id = await _running_task_map(runs)
    items = [_running_task_item(run, tasks_by_id.get(run.task_id)) for run in runs]
    paginated = items[offset : offset + limit]
    return success_response(paginated, message=Messages.QUERY_SUCCESS)


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


async def get_tasks_stats(project_id: str | None, current_user):
    """获取任务统计信息（全局/按项目）"""
    task_query = await _task_stats_query(current_user.user_id, project_id)
    counts = await _task_status_counts(task_query)
    scheduled_tasks = await task_query.filter(
        schedule_type__in=[ScheduleType.CRON, ScheduleType.INTERVAL, ScheduleType.DATE]
    ).count()
    run_stats = await _task_run_stats(task_query)
    data = _task_stats_payload(counts=counts, scheduled_tasks=scheduled_tasks, run_stats=run_stats)
    return success_response(data, message=Messages.QUERY_SUCCESS)


async def list_task_runs(
    task_id: str,
    *,
    page: int,
    size: int,
    status: str | None,
    start_date: str | None,
    end_date: str | None,
    current_user,
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


async def get_task_schedule_history(
    task_id: str,
    *,
    page: int,
    size: int,
    start_date: str | None,
    end_date: str | None,
    current_user,
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


async def get_task_stats(task_id, current_user):
    """获取任务统计信息"""
    from loguru import logger

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


def register_query_routes(router, *, running_task_hard_cap: int) -> None:
    @router.get("/running", response_model=BaseResponse[list])
    async def _get_running_tasks(
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
        return await get_running_tasks(
            offset=offset,
            limit=limit,
            current_user=current_user,
            running_task_hard_cap=running_task_hard_cap,
        )

    @router.get("/stats", response_model=BaseResponse[dict])
    async def _get_tasks_stats(
        project_id: str | None = Query(None),
        current_user=Depends(get_current_user),
    ):
        """获取任务统计信息（全局/按项目）"""
        return await get_tasks_stats(project_id, current_user)

    @router.get("/{task_id}/runs", response_model=PaginationResponse[TaskRunResponse])
    async def _list_task_runs(
        task_id: str,
        *,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        current_user=Depends(get_current_user),
    ):
        """获取任务运行历史"""
        return await list_task_runs(
            task_id,
            page=page,
            size=size,
            status=status,
            start_date=start_date,
            end_date=end_date,
            current_user=current_user,
        )

    @router.get("/{task_id}/schedule-history", response_model=PaginationResponse[TaskRunResponse])
    async def _get_task_schedule_history(
        task_id: str,
        *,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        start_date: str | None = Query(None),
        end_date: str | None = Query(None),
        current_user=Depends(get_current_user),
    ):
        """获取任务调度历史（复用执行记录）"""
        return await get_task_schedule_history(
            task_id,
            page=page,
            size=size,
            start_date=start_date,
            end_date=end_date,
            current_user=current_user,
        )

    @router.get("/{task_id}/stats", response_model=BaseResponse[TaskStatsResponse])
    async def _get_task_stats(task_id, current_user=Depends(get_current_user)):
        """获取任务统计信息"""
        return await get_task_stats(task_id, current_user)


__all__ = [
    "get_running_tasks",
    "get_task_schedule_history",
    "get_task_stats",
    "get_tasks_stats",
    "list_task_runs",
    "register_query_routes",
]
