"""仪表盘接口"""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from src.core.security.auth import get_current_user
from src.schemas.common import BaseResponse
from src.core.response import success, Messages
from src.models.project import Project
from src.models.scheduler import ScheduledTask
from src.models.enums import ProjectStatus, ProjectType, TaskStatus

router = APIRouter()


@router.get("/summary", response_model=BaseResponse[dict], summary="仪表盘摘要")
async def get_dashboard_summary(current_user=Depends(get_current_user)):
    try:
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
                },
            },
        }

        return success(data, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取仪表盘摘要失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取摘要失败"
        )


@router.get("/metrics", response_model=BaseResponse[dict], summary="系统指标")
async def get_system_metrics(current_user=Depends(get_current_user)):
    try:
        from src.infrastructure.cache import system_metrics_service
        metrics = await system_metrics_service.get_metrics()
        return success(metrics.model_dump(), message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取系统指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取指标失败: {str(e)}")


@router.get(
    "/metrics/cache-info",
    response_model=BaseResponse[dict],
    summary="指标缓存信息"
)
async def get_metrics_cache_info(current_user=Depends(get_current_user)):
    from src.services.scheduler.scheduler_service import scheduler_service
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        from src.infrastructure.cache import system_metrics_service
        cache_info = await system_metrics_service.get_cache_info()
        return success(cache_info, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取缓存信息失败: {e}")
        raise HTTPException(
            status_code=500, detail=f"获取缓存信息失败: {str(e)}"
        )


@router.post("/metrics/refresh", response_model=BaseResponse[dict], summary="刷新指标")
async def refresh_system_metrics(current_user=Depends(get_current_user)):
    try:
        from src.infrastructure.cache import system_metrics_service
        metrics = await system_metrics_service.get_metrics(force_refresh=True)
        return success(metrics.model_dump(), message="指标已刷新")
    except Exception as e:
        logger.error(f"刷新指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"刷新指标失败: {str(e)}")


@router.delete(
    "/metrics/cache",
    response_model=BaseResponse[None],
    summary="清除指标缓存"
)
async def clear_metrics_cache(current_user=Depends(get_current_user)):
    from src.services.scheduler.scheduler_service import scheduler_service
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        from src.infrastructure.cache import system_metrics_service
        await system_metrics_service.clear_cache()
        return success(None, message="缓存已清除")
    except Exception as e:
        logger.error(f"清除缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清除缓存失败: {str(e)}")
