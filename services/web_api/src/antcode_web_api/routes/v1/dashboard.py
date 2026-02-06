"""仪表盘接口"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from antcode_web_api.response import Messages, success
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models.enums import ProjectStatus, ProjectType, TaskStatus
from antcode_core.domain.models.project import Project
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.application.services.monitoring import system_metrics_service

router = APIRouter()


@router.get("/summary", response_model=BaseResponse[dict], summary="仪表盘摘要")
async def get_dashboard_summary(current_user=Depends(get_current_user)):
    try:
        # 并行执行所有 count 查询，减少总耗时
        (
            total_projects,
            projects_active,
            projects_inactive,
            projects_draft,
            projects_archived,
            projects_file,
            projects_rule,
            projects_code,
            total_tasks,
            tasks_active,
            tasks_running,
            tasks_success,
            tasks_failed,
            tasks_paused,
        ) = await asyncio.gather(
            Project.all().count(),
            Project.filter(status=ProjectStatus.ACTIVE).count(),
            Project.filter(status=ProjectStatus.INACTIVE).count(),
            Project.filter(status=ProjectStatus.DRAFT).count(),
            Project.filter(status=ProjectStatus.ARCHIVED).count(),
            Project.filter(type=ProjectType.FILE).count(),
            Project.filter(type=ProjectType.RULE).count(),
            Project.filter(type=ProjectType.CODE).count(),
            Task.all().count(),
            Task.filter(is_active=True).count(),
            Task.filter(status=TaskStatus.RUNNING).count(),
            Task.filter(status=TaskStatus.SUCCESS).count(),
            Task.filter(status=TaskStatus.FAILED).count(),
            Task.filter(status=TaskStatus.PAUSED).count(),
        )

        data = {
            "projects": {
                "total": total_projects,
                "by_status": {
                    "active": projects_active,
                    "inactive": projects_inactive,
                    "draft": projects_draft,
                    "archived": projects_archived,
                },
                "by_type": {
                    "file": projects_file,
                    "rule": projects_rule,
                    "code": projects_code,
                },
            },
            "tasks": {
                "total": total_tasks,
                "active": tasks_active,
                "running": tasks_running,
                "by_status": {
                    "success": tasks_success,
                    "failed": tasks_failed,
                    "paused": tasks_paused,
                },
            },
        }

        return success(data, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取仪表盘摘要失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取摘要失败"
        )


@router.get("/metrics", response_model=BaseResponse[dict], summary="系统指标")
async def get_system_metrics(current_user=Depends(get_current_user)):
    try:
        metrics = await system_metrics_service.get_metrics()
        return success(metrics.model_dump(), message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取系统指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取指标失败: {str(e)}")


@router.get("/metrics/cache-info", response_model=BaseResponse[dict], summary="指标缓存信息")
async def get_metrics_cache_info(current_user=Depends(get_current_user)):
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        cache_info = await system_metrics_service.get_cache_info()
        return success(cache_info, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取缓存信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取缓存信息失败: {str(e)}")


@router.post("/metrics/refresh", response_model=BaseResponse[dict], summary="刷新指标")
async def refresh_system_metrics(current_user=Depends(get_current_user)):
    try:
        metrics = await system_metrics_service.get_metrics(force_refresh=True)
        return success(metrics.model_dump(), message="指标已刷新")
    except Exception as e:
        logger.error(f"刷新指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"刷新指标失败: {str(e)}")


@router.delete("/metrics/cache", response_model=BaseResponse[None], summary="清除指标缓存")
async def clear_metrics_cache(current_user=Depends(get_current_user)):
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        await system_metrics_service.clear_cache()
        return success(None, message="缓存已清除")
    except Exception as e:
        logger.error(f"清除缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清除缓存失败: {str(e)}")


@router.get("/tasks/hourly-trend", response_model=BaseResponse[list], summary="24小时任务趋势")
async def get_tasks_hourly_trend(current_user=Depends(get_current_user)):
    """获取过去24小时每小时的任务完成数量趋势"""
    try:
        now = datetime.now()
        # 获取24小时前的时间点
        start_time = now - timedelta(hours=24)

        # 查询过去24小时所有已完成的任务执行记录
        executions = await TaskRun.filter(
            start_time__gte=start_time,
            status__in=[TaskStatus.SUCCESS, TaskStatus.FAILED]
        ).all()

        # 按小时分组统计
        hourly_data = {}
        for i in range(24):
            hour_start = now - timedelta(hours=24 - i)
            hour_key = hour_start.hour
            hourly_data[hour_key] = {"hour": hour_key, "tasks": 0, "success": 0, "failed": 0}

        for execution in executions:
            hour_key = execution.start_time.hour
            if hour_key in hourly_data:
                hourly_data[hour_key]["tasks"] += 1
                if execution.status == TaskStatus.SUCCESS:
                    hourly_data[hour_key]["success"] += 1
                elif execution.status == TaskStatus.FAILED:
                    hourly_data[hour_key]["failed"] += 1

        # 按小时顺序排列（从0点到23点）
        result = [hourly_data[i] for i in range(24)]

        return success(result, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取任务趋势失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务趋势失败: {str(e)}")
