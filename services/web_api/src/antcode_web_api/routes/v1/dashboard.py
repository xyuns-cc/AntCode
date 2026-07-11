"""仪表盘接口"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from antcode_core.application.services.monitoring import system_metrics_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import UserRole
from antcode_core.domain.models.enums import ProjectStatus, ProjectType, TaskStatus
from antcode_core.domain.models.project import Project
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.infrastructure.cache import unified_cache
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from antcode_web_api.deps import require_role
from antcode_web_api.response import Messages, success

router = APIRouter()

# Dashboard 摘要缓存 30 秒：仪表盘轮询频繁但数据变化慢，
# 每个用户看到的是全局聚合统计，可跨用户共享缓存。
_SUMMARY_CACHE_KEY = "dashboard:summary:v1"
_SUMMARY_CACHE_TTL = 30
# T7-B1b (P1-4): metrics/hourly-trend 端点跟 summary 一样每次都被前端轮询，
# 但之前**没有**缓存。metrics 直接跳 system_metrics_service（PG + Redis
# 聚合），hourly-trend 全量拉 24h 的 task_executions，都是重路径。
# metrics 短一点（15s），hourly-trend 长一点（60s，趋势天然慢变）。
_METRICS_CACHE_KEY = "dashboard:metrics:v1"
_METRICS_CACHE_TTL = 15
_HOURLY_TREND_CACHE_KEY = "dashboard:tasks:hourly-trend:v1"
_HOURLY_TREND_CACHE_TTL = 60


# P2-19: /summary 与 /metrics 返回的是**全库**聚合(所有 Project/Task),
# 任何登录用户都能看到。产品定位:仪表盘是"平台级"概览而非"我的",
# 因此保留全局可见;若日后需要多租户按 owner 隔离,应在这里补上 owner
# filter(参考 projects/tasks 路由里 non-admin 的 owner_id filter 模式)。
# 与之相对,写路径(/metrics/refresh)和管理路径(cache-info、cache 清理)
# 已在下方收紧到 ADMIN/SUPER_ADMIN,避免普通用户触发 CPU 采样 / 缓存清空。
@router.get("/summary", response_model=BaseResponse[dict], summary="仪表盘摘要")
async def get_dashboard_summary(current_user=Depends(get_current_user)):
    try:
        cache_key = _SUMMARY_CACHE_KEY if current_user.is_admin else f"{_SUMMARY_CACHE_KEY}:user:{current_user.user_id}"
        cached = await unified_cache.get(cache_key)
        if cached is not None:
            return success(cached, message=Messages.QUERY_SUCCESS)
        project_scope = {} if current_user.is_admin else {"user_id": current_user.user_id}
        task_scope = {} if current_user.is_admin else {"user_id": current_user.user_id}
        # 并行执行所有 count 查询，减少总耗时
        (
            total_projects,
            projects_active,
            projects_inactive,
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
            Project.filter(**project_scope).count(),
            Project.filter(status=ProjectStatus.ACTIVE, **project_scope).count(),
            Project.filter(status=ProjectStatus.INACTIVE, **project_scope).count(),
            Project.filter(status=ProjectStatus.ARCHIVED, **project_scope).count(),
            Project.filter(type=ProjectType.FILE, **project_scope).count(),
            Project.filter(type=ProjectType.RULE, **project_scope).count(),
            Project.filter(type=ProjectType.CODE, **project_scope).count(),
            Task.filter(**task_scope).count(),
            Task.filter(is_active=True, **task_scope).count(),
            Task.filter(status=TaskStatus.RUNNING, **task_scope).count(),
            Task.filter(status=TaskStatus.SUCCESS, **task_scope).count(),
            Task.filter(status=TaskStatus.FAILED, **task_scope).count(),
            Task.filter(status=TaskStatus.PAUSED, **task_scope).count(),
        )

        data = {
            "projects": {
                "total": total_projects,
                "by_status": {
                    "active": projects_active,
                    "inactive": projects_inactive,
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

        await unified_cache.set(cache_key, data, ttl=_SUMMARY_CACHE_TTL)
        return success(data, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取仪表盘摘要失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取摘要失败")


@router.get(
    "/metrics",
    response_model=BaseResponse[dict],
    summary="系统指标",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_system_metrics(current_user=Depends(get_current_user)):
    try:
        # T7-B1b: 与 summary 同款 unified_cache 兜住轮询；15s TTL 平衡新鲜度
        # 与 PG/Redis 压力
        cached = await unified_cache.get(_METRICS_CACHE_KEY)
        if cached is not None:
            return success(cached, message=Messages.QUERY_SUCCESS)
        metrics = await system_metrics_service.get_metrics()
        data = metrics.model_dump()
        await unified_cache.set(_METRICS_CACHE_KEY, data, ttl=_METRICS_CACHE_TTL)
        return success(data, message=Messages.QUERY_SUCCESS)
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


# P2-19: /metrics/refresh 会强制重跑 CPU 采样 + PG/Redis 聚合,属于重路径。
# 相邻的 /metrics/cache-info 和 DELETE /metrics/cache 都是 admin-only,
# 这里对齐,防止普通用户绕过 15s 缓存持续压后端。
@router.post(
    "/metrics/refresh",
    response_model=BaseResponse[dict],
    summary="刷新指标",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
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
        # T7-B1b: 趋势数据 60s 内不会有可感知变化，缓存兜住前端轮询
        cache_key = (
            _HOURLY_TREND_CACHE_KEY
            if current_user.is_admin
            else f"{_HOURLY_TREND_CACHE_KEY}:user:{current_user.user_id}"
        )
        cached = await unified_cache.get(cache_key)
        if cached is not None:
            return success(cached, message=Messages.QUERY_SUCCESS)
        now = datetime.now()
        # 获取24小时前的时间点
        start_time = now - timedelta(hours=24)

        # 查询过去24小时所有已完成的任务执行记录
        run_scope = {} if current_user.is_admin else {"created_by": current_user.user_id}
        executions = await TaskRun.filter(
            start_time__gte=start_time,
            status__in=[TaskStatus.SUCCESS, TaskStatus.FAILED],
            **run_scope,
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

        await unified_cache.set(cache_key, result, ttl=_HOURLY_TREND_CACHE_TTL)
        return success(result, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取任务趋势失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务趋势失败: {str(e)}")
