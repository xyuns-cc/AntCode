"""
仪表板汇总统计（不区分角色）
提供全量项目与任务的聚合统计，供前端仪表板使用
"""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from src.core.auth import get_current_user  # 需要登录，但不做角色区分
from src.schemas.common import BaseResponse
from src.core.response import success, error
from src.core.response import Messages, ResponseCode
from src.models.project import Project
from src.models.scheduler import ScheduledTask
from src.models.enums import ProjectStatus, ProjectType, TaskStatus


router = APIRouter()


@router.get("/summary", response_model=BaseResponse[dict], summary="仪表板汇总统计")
async def get_dashboard_summary(current_user=Depends(get_current_user)):
    """返回全量项目与任务统计（不区分用户角色）"""
    try:
        # 项目统计
        total_projects = await Project.all().count()
        projects_by_status = {
            "active": await Project.filter(status=ProjectStatus.ACTIVE).count(),
            "inactive": await Project.filter(status=ProjectStatus.INACTIVE).count(),
            "draft": await Project.filter(status=ProjectStatus.DRAFT).count(),
            "archived": await Project.filter(status=ProjectStatus.ARCHIVED).count(),
        }
        projects_by_type = {
            "file": await Project.filter(type=ProjectType.FILE).count(),
            "rule": await Project.filter(type=ProjectType.RULE).count(),
            "code": await Project.filter(type=ProjectType.CODE).count(),
        }

        # 任务统计
        total_tasks = await ScheduledTask.all().count()
        tasks_active = await ScheduledTask.filter(is_active=True).count()
        tasks_running = await ScheduledTask.filter(status=TaskStatus.RUNNING).count()
        tasks_completed = await ScheduledTask.filter(status=TaskStatus.SUCCESS).count()
        tasks_failed = await ScheduledTask.filter(status=TaskStatus.FAILED).count()
        tasks_paused = await ScheduledTask.filter(status=TaskStatus.PAUSED).count()

        data = {
            "projects": {
                "total": total_projects,
                "by_status": projects_by_status,
                "by_type": projects_by_type,
            },
            "tasks": {
                "total": total_tasks,
                "active": tasks_active,
                "running": tasks_running,
                "by_status": {
                    "completed": tasks_completed,
                    "failed": tasks_failed,
                    "paused": tasks_paused,
                    # 其余状态可以根据需要继续补充
                },
            },
        }

        return success(data, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取仪表板汇总统计失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取仪表板汇总统计失败"
        )


@router.get("/metrics", response_model=BaseResponse[dict], summary="系统运行指标")
async def get_system_metrics(current_user=Depends(get_current_user)):
    """获取系统运行指标（CPU/内存/磁盘/活跃任务/运行时长）。"""
    try:
        from src.utils.metrics_cache import system_metrics_service
        metrics = await system_metrics_service.get_metrics()
        return success(metrics.model_dump(), message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取系统指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取系统指标失败: {str(e)}")


@router.get("/metrics/cache-info", response_model=BaseResponse[dict], summary="指标缓存信息")
async def get_metrics_cache_info(current_user=Depends(get_current_user)):
    """获取系统指标缓存状态（管理员）。"""
    from src.services.scheduler.scheduler_service import scheduler_service
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以访问缓存信息")
    try:
        from src.utils.metrics_cache import system_metrics_service
        cache_info = await system_metrics_service.get_cache_info()
        return success(cache_info, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取缓存信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取缓存信息失败: {str(e)}")


@router.post("/metrics/refresh", response_model=BaseResponse[dict], summary="刷新系统指标")
async def refresh_system_metrics(current_user=Depends(get_current_user)):
    """强制刷新系统指标缓存。"""
    try:
        from src.utils.metrics_cache import system_metrics_service
        metrics = await system_metrics_service.get_metrics(force_refresh=True)
        return success(metrics.model_dump(), message="指标已刷新")
    except Exception as e:
        logger.error(f"刷新系统指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"刷新系统指标失败: {str(e)}")


@router.delete("/metrics/cache", response_model=BaseResponse[None], summary="清除指标缓存")
async def clear_metrics_cache(current_user=Depends(get_current_user)):
    """清除系统指标缓存（管理员）。"""
    from src.services.scheduler.scheduler_service import scheduler_service
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以清除缓存")
    try:
        from src.utils.metrics_cache import system_metrics_service
        await system_metrics_service.clear_cache()
        return success(None, message="缓存已清除")
    except Exception as e:
        logger.error(f"清除缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清除缓存失败: {str(e)}")
