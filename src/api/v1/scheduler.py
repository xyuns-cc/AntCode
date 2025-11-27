"""任务调度接口"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from tortoise.exceptions import DoesNotExist, IntegrityError

from src.core.auth import get_current_user, TokenData
from src.core.response import success as success_response, task_list, execution_list, Messages
from src.models.enums import TaskStatus, ProjectType
from src.schemas.common import BaseResponse
from src.schemas.scheduler import (
    TaskCreate, TaskUpdate, TaskResponse, ExecutionResponse, LogFileResponse,
    TaskStatsResponse, SystemMetricsResponse, TaskListResponse, ExecutionListResponse
)
from src.services.logs.task_log_service import task_log_service
from src.services.projects.relation_service import relation_service
from src.services.scheduler.scheduler_service import scheduler_service
from src.services.scheduler.task_executor import TaskExecutor
from src.services.users.user_service import user_service
from src.utils.api_optimizer import fast_response

router = APIRouter()


def create_task_response(task):
    return TaskResponse.model_construct(
        id=task.id,
        name=task.name,
        description=task.description,
        project_id=task.project_id,
        schedule_type=task.schedule_type,
        is_active=task.is_active,
        task_type=task.task_type,
        status=task.status,
        cron_expression=task.cron_expression,
        interval_seconds=task.interval_seconds,
        scheduled_time=task.scheduled_time,
        last_run_time=task.last_run_time,
        next_run_time=task.next_run_time,
        created_at=task.created_at,
        updated_at=task.updated_at,
        created_by=getattr(task, 'created_by', task.user_id),
        created_by_username=getattr(task, 'created_by_username', None)
    )


@router.post("/tasks", response_model=BaseResponse[TaskResponse])
async def create_task(task_data: TaskCreate, current_user=Depends(get_current_user)):
    # 验证项目权限
    if not await relation_service.validate_project_user(task_data.project_id, current_user.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found or access denied"
        )

    # 获取项目信息
    project_info = await relation_service.get_project_with_details(task_data.project_id)
    if not project_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    project = project_info["project"]

    try:
        # 使用service层创建任务
        task = await scheduler_service.create_task(
            task_data=task_data,
            project_type=ProjectType(project.type),
            user_id=current_user.user_id
        )

        return success_response(create_task_response(task), message=Messages.CREATED_SUCCESS)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task name already exists"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/tasks", response_model=TaskListResponse)
@fast_response(cache_ttl=60, namespace="scheduler:list")
async def list_tasks(
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        status: str = None,
        is_active: bool = None,
        current_user=Depends(get_current_user)
):
    try:
        is_admin = await user_service.is_admin(current_user.user_id)
        user_filter = None if is_admin else current_user.user_id
        
        result = await scheduler_service.get_user_tasks(
            user_id=user_filter,
            status=status,
            is_active=is_active,
            page=page,
            size=size
        )
        
        # 构建 TaskResponse 对象列表
        task_responses = [create_task_response(task) for task in result["tasks"]]
        
        return task_list(
            total=result["total"],
            page_num=result["page"],
            size=result["size"],
            items=task_responses
        )
    except Exception as e:
        logger.error(f"Failed to list tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tasks")


@router.get("/tasks/{task_id}", response_model=BaseResponse[TaskResponse])
@fast_response(cache_ttl=60, namespace="scheduler:detail", key_prefix_fn=lambda args, kwargs: str(kwargs.get('task_id') if 'task_id' in kwargs else (args[0] if args else '')))
async def get_task(task_id, current_user=Depends(get_current_user)):
    try:
        task = await scheduler_service.get_task_by_id(task_id, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(create_task_response(task), message=Messages.QUERY_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task: {e}")
        raise HTTPException(status_code=500, detail="Failed to get task")


@router.put("/tasks/{task_id}", response_model=BaseResponse[TaskResponse])
async def update_task(task_id, task_data: TaskUpdate, current_user=Depends(get_current_user)):
    try:
        task = await scheduler_service.update_task(task_id, task_data, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(create_task_response(task), message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task: {e}")
        raise HTTPException(status_code=500, detail="Failed to update task")


@router.delete("/tasks/{task_id}", response_model=BaseResponse)
async def delete_task(task_id, current_user=Depends(get_current_user)):
    try:
        deleted = await scheduler_service.delete_task(task_id, current_user.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message=Messages.DELETED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete task: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete task")


@router.post("/tasks/{task_id}/pause", response_model=BaseResponse)
async def pause_task(
        task_id,
        current_user=Depends(get_current_user)
):
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


@router.post("/tasks/{task_id}/resume", response_model=BaseResponse)
async def resume_task(
        task_id,
        current_user=Depends(get_current_user)
):
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


@router.post("/tasks/{task_id}/trigger", response_model=BaseResponse)
async def trigger_task(
        task_id,
        current_user=Depends(get_current_user)
):
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


@router.get("/tasks/{task_id}/executions", response_model=ExecutionListResponse)
async def list_task_executions(
        task_id: int,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        status: str = None,
        start_date: str = None,
        end_date: str = None,
        current_user=Depends(get_current_user)
):
    """获取任务执行历史"""
    try:
        result = await scheduler_service.get_task_executions(
            task_id=task_id,
            user_id=current_user.user_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
            size=size
        )
        
        return execution_list(
            total=result["total"],
            page_num=result["page"],
            size=result["size"],
            items=[ExecutionResponse.from_orm(e) for e in result["executions"]]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"获取任务执行历史失败: {e}")
        raise HTTPException(status_code=500, detail="获取任务执行历史失败")


@router.get("/executions/{execution_id}", response_model=BaseResponse[ExecutionResponse])
async def get_execution(
        execution_id,
        current_user=Depends(get_current_user)
):
    """获取执行详情"""
    try:
        execution = await scheduler_service.get_execution_with_permission(execution_id, current_user.user_id)
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

        return success_response(ExecutionResponse.from_orm(execution), message=Messages.QUERY_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取执行详情失败: {e}")
        raise HTTPException(status_code=500, detail="获取执行详情失败")


@router.get("/executions/{execution_id}/logs/file", response_model=BaseResponse[LogFileResponse])
async def get_execution_log_file(
        execution_id: int,
        log_type: str = Query("output", regex="^(output|error)$"),
        lines: int = Query(None, ge=1, le=10000),
        current_user=Depends(get_current_user)
):
    """获取执行日志文件内容"""
    try:
        execution = await scheduler_service.get_execution_with_permission(execution_id, current_user.user_id)
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

        # 确定日志文件路径
        if log_type == "output":
            log_file_path = execution.log_file_path
        else:  # error
            log_file_path = execution.error_log_path

        if not log_file_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="日志文件路径不存在"
            )

        # 读取日志文件内容
        content = await task_log_service.read_log(log_file_path, lines)
        log_info = await task_log_service.get_log_info(log_file_path)

        return success_response(
            LogFileResponse(
                execution_id=execution_id,
                log_type=log_type,
                content=content,
                file_path=log_file_path,
                file_size=log_info.get("size", 0),
                lines_count=log_info.get("lines", 0),
                last_modified=log_info.get("modified_time")
            ),
            message=Messages.QUERY_SUCCESS
        )

    except DoesNotExist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="执行记录不存在"
        )


@router.get("/tasks/{task_id}/stats", response_model=BaseResponse[TaskStatsResponse])
async def get_task_stats(
        task_id,
        current_user=Depends(get_current_user)
):
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
            average_duration=stats_data["avg_duration"]
        )

        return success_response(stats, message=Messages.QUERY_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取任务统计失败")


# 监控指标相关接口已迁移至 /api/v1/dashboard


@router.get("/running", response_model=BaseResponse[list])
@fast_response(cache_ttl=10, namespace="scheduler:running")
async def get_running_tasks(
        current_user=Depends(get_current_user)
):
    """获取运行中的任务"""
    running = scheduler_service.get_running_tasks()

    # 过滤只显示当前用户的任务
    user_tasks = []
    for task_info in running:
        task = await scheduler_service.get_task_by_id(task_info['task_id'], current_user.user_id)
        if task:
            user_tasks.append(task_info)

    return success_response(user_tasks, message=Messages.QUERY_SUCCESS)


@router.post("/cleanup-workspaces", response_model=BaseResponse)
async def cleanup_workspaces(
        max_age_hours: int = Query(default=24, ge=0),
        current_user=Depends(get_current_user)
):
    """手动清理执行工作目录"""
    # 只有管理员可以执行清理操作
    is_admin = await scheduler_service.verify_admin_permission(current_user.user_id)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以执行清理操作"
        )
    
    try:
        executor = TaskExecutor()
        await executor.cleanup_old_workspaces(max_age_hours=max_age_hours)
        
        return success_response(None, message=f"清理完成，已删除超过 {max_age_hours} 小时的工作目录")
    except Exception as e:
        logger.error(f"手动清理工作目录失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清理失败: {str(e)}"
        )
