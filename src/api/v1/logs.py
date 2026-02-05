"""日志管理接口"""

import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from tortoise.exceptions import DoesNotExist

from src.core.security.auth import get_current_user
from src.schemas.common import BaseResponse
from src.core.response import success
from src.core.response import Messages
from src.schemas.logs import LogEntry, LogListResponse, UnifiedLogResponse, LogLevel, LogType, LogFormat
from src.services.logs.log_security_service import log_security_service, error_handler
from src.services.logs.task_log_service import task_log_service
from src.services.scheduler.scheduler_service import scheduler_service
from src.services.websockets.websocket_connection_manager import websocket_manager

router = APIRouter()


def _parse_enum(value, enum_cls, field_name):
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 参数无效"
        )


async def _get_raw_log_response(execution_id, execution, log_type, lines):
    try:
        file_path = None
        content = ""
        file_size = 0
        lines_count = 0
        last_modified = None

        if log_type == LogType.STDOUT and execution.log_file_path:
            file_path = execution.log_file_path
        elif log_type == LogType.STDERR and execution.error_log_path:
            file_path = execution.error_log_path
        else:
            # 如果没有指定类型，合并两个文件的内容
            logs_data = await task_log_service.get_execution_logs(execution_id)
            stdout_content = logs_data.get("output", "")
            stderr_content = logs_data.get("error", "")

            if stdout_content and stderr_content:
                content = f"=== STDOUT ===\n{stdout_content}\n\n=== STDERR ===\n{stderr_content}"
            elif stdout_content:
                content = stdout_content
            elif stderr_content:
                content = stderr_content
            else:
                content = ""

            if lines:
                content_lines = content.split('\n')
                content = '\n'.join(content_lines[-lines:] if len(content_lines) > lines else content_lines)

            return success(
                UnifiedLogResponse(
                    execution_id=execution_id,
                    format=LogFormat.RAW,
                    log_type=log_type.value if log_type else "mixed",
                    raw_content=content,
                    lines_count=len(content.split('\n')) if content else 0
                ),
                message=Messages.QUERY_SUCCESS
            )

        # 读取指定文件
        if file_path:
            content = await task_log_service.read_log(file_path, lines)
            log_info = await task_log_service.get_log_info(file_path)
            file_size = log_info.get("size", 0)
            lines_count = log_info.get("lines", 0)
            last_modified = log_info.get("modified_time")

        return success(
            UnifiedLogResponse(
                execution_id=execution_id,
                format=LogFormat.RAW,
                log_type=log_type.value if log_type else None,
                raw_content=content,
                file_path=file_path,
                file_size=file_size,
                lines_count=lines_count,
                last_modified=last_modified
            ),
            message=Messages.QUERY_SUCCESS
        )

    except Exception as e:
        logger.error(f"获取原始日志失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取日志失败")


async def _get_structured_log_response(execution_id, execution, log_type, level, lines, search):
    try:
        # 获取日志内容
        logs_data = await task_log_service.get_execution_logs(execution_id)

        # 解析日志条目
        log_entries = []

        # 处理标准输出日志
        if not log_type or log_type == LogType.STDOUT:
            stdout_lines = logs_data.get("output", "").split('\n')
            for i, line in enumerate(stdout_lines):
                if line.strip():
                    if not search or search.lower() in line.lower():
                        log_entries.append(LogEntry(
                            id=i,
                            timestamp=execution.start_time,
                            level=LogLevel.INFO,
                            log_type=LogType.STDOUT,
                            execution_id=execution_id,
                            task_id=getattr(execution, "task_public_id", None),
                            message=line.strip(),
                            source="task_execution"
                        ))

        # 处理错误输出日志
        if not log_type or log_type == LogType.STDERR:
            stderr_lines = logs_data.get("error", "").split('\n')
            for i, line in enumerate(stderr_lines):
                if line.strip():
                    if not search or search.lower() in line.lower():
                        log_entries.append(LogEntry(
                            id=len(log_entries) + i,
                            timestamp=execution.start_time,
                            level=LogLevel.ERROR,
                            log_type=LogType.STDERR,
                            execution_id=execution_id,
                            task_id=getattr(execution, "task_public_id", None),
                            message=line.strip(),
                            source="task_execution"
                        ))

        # 按级别过滤
        if level:
            log_entries = [entry for entry in log_entries if entry.level == level]

        # 限制行数
        if lines:
            log_entries = log_entries[-lines:]

        structured_data = LogListResponse(
            total=len(log_entries),
            page=1,
            size=len(log_entries),
            items=log_entries
        )

        return success(
            UnifiedLogResponse(
                execution_id=execution_id,
                format=LogFormat.STRUCTURED,
                log_type=log_type.value if log_type else None,
                structured_data=structured_data
            ),
            message=Messages.QUERY_SUCCESS
        )

    except Exception as e:
        logger.error(f"获取结构化日志失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取日志失败")


@router.get("/executions/{execution_id}", response_model=BaseResponse[UnifiedLogResponse])
async def get_execution_logs(
        execution_id,
        format: str = Query(LogFormat.STRUCTURED),
        log_type: str = Query(None),
        level: str = Query(None),
        lines: int = Query(None, ge=1, le=10000),
        search: str = Query(None),
        current_user=Depends(get_current_user)
):
    try:
        format_value = _parse_enum(format, LogFormat, "format") or LogFormat.STRUCTURED
        log_type_value = _parse_enum(log_type, LogType, "log_type")
        level_value = _parse_enum(level, LogLevel, "level")

        # 使用增强的权限验证
        execution = await log_security_service.verify_log_access_permission(
            current_user, execution_id, "read"
        )

        # 根据格式返回不同的响应
        if format_value == LogFormat.RAW:
            # 返回原始文本格式
            return await _get_raw_log_response(execution_id, execution, log_type_value, lines)
        else:
            # 返回结构化格式
            return await _get_structured_log_response(
                execution_id,
                execution,
                log_type_value,
                level_value,
                lines,
                search
            )

    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        # 使用增强的错误处理
        error_id = error_handler.log_error(e, {
            "endpoint": "get_execution_logs",
            "execution_id": execution_id,
            "user_id": current_user.user_id,
            "format": format,
            "log_type": log_type
        })

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取执行日志失败 (error_id: {error_id})"
        )


# 便捷接口：直接获取特定类型的日志

@router.get("/executions/{execution_id}/stdout", response_model=BaseResponse[UnifiedLogResponse])
async def get_stdout_logs(
        execution_id,
        format: str = Query(LogFormat.RAW),
        lines: int = Query(None, ge=1, le=10000),
        current_user=Depends(get_current_user)
):
    """获取标准输出日志"""
    return await get_execution_logs(
        execution_id=execution_id,
        format=format,
        log_type=LogType.STDOUT,
        lines=lines,
        current_user=current_user
    )


@router.get("/executions/{execution_id}/stderr", response_model=BaseResponse[UnifiedLogResponse])
async def get_stderr_logs(
        execution_id,
        format: str = Query(LogFormat.RAW),
        lines: int = Query(None, ge=1, le=10000),
        current_user=Depends(get_current_user)
):
    """获取标准错误输出日志"""
    return await get_execution_logs(
        execution_id=execution_id,
        format=format,
        log_type=LogType.STDERR,
        lines=lines,
        current_user=current_user
    )


@router.get("/executions/{execution_id}/errors", response_model=BaseResponse[UnifiedLogResponse])
async def get_error_logs(
        execution_id,
        format: str = Query(LogFormat.STRUCTURED),
        lines: int = Query(None, ge=1, le=10000),
        search: str = Query(None),
        current_user=Depends(get_current_user)
):
    """获取错误级别的日志"""
    return await get_execution_logs(
        execution_id=execution_id,
        format=format,
        level=LogLevel.ERROR,
        lines=lines,
        search=search,
        current_user=current_user
    )


@router.get("/executions/{execution_id}/raw", response_model=BaseResponse[UnifiedLogResponse])
async def get_raw_logs(
        execution_id,
        log_type: str = Query(None),
        lines: int = Query(None, ge=1, le=10000),
        current_user=Depends(get_current_user)
):
    """获取原始格式的日志"""
    return await get_execution_logs(
        execution_id=execution_id,
        format=LogFormat.RAW,
        log_type=log_type,
        lines=lines,
        current_user=current_user
    )


@router.get("/tasks/{task_id}", response_model=BaseResponse[LogListResponse])
async def get_task_logs(
        task_id,
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=1000),
        log_type: str = Query(None),
        level: str = Query(None),
        start_time: str = Query(None),
        end_time: str = Query(None),
        search: str = Query(None),
        current_user=Depends(get_current_user)
):
    """获取指定任务的所有日志"""
    try:
        # 验证任务权限并获取执行记录
        result = await scheduler_service.get_task_executions(
            task_id=task_id,
            user_id=current_user.user_id,
            start_date=start_time,
            end_date=end_time,
            page=page,
            size=size
        )

        executions = result["executions"]
        total_executions = result["total"]

        # 收集所有日志条目
        all_log_entries = []

        for execution in executions:
            try:
                logs_data = await task_log_service.get_execution_logs(execution.execution_id)

                # 处理标准输出日志
                if not log_type or log_type == LogType.STDOUT:
                    stdout_lines = logs_data.get("output", "").split('\n')
                    for line in stdout_lines:
                        if line.strip():
                            if not search or search.lower() in line.lower():
                                all_log_entries.append(LogEntry(
                                    timestamp=execution.start_time,
                                    level=LogLevel.INFO,
                                    log_type=LogType.STDOUT,
                                    execution_id=execution.execution_id,
                                    task_id=task_id,
                                    message=line.strip(),
                                    source="task_execution"
                                ))

                # 处理错误输出日志
                if not log_type or log_type == LogType.STDERR:
                    stderr_lines = logs_data.get("error", "").split('\n')
                    for line in stderr_lines:
                        if line.strip():
                            if not search or search.lower() in line.lower():
                                all_log_entries.append(LogEntry(
                                    timestamp=execution.start_time,
                                    level=LogLevel.ERROR,
                                    log_type=LogType.STDERR,
                                    execution_id=execution.execution_id,
                                    task_id=task_id,
                                    message=line.strip(),
                                    source="task_execution"
                                ))
            except Exception as e:
                logger.warning(f"读取执行记录 {execution.execution_id} 日志失败: {e}")
                continue

        # 按级别过滤
        if level:
            all_log_entries = [entry for entry in all_log_entries if entry.level == level]

        # 按时间排序
        all_log_entries.sort(key=lambda x: x.timestamp, reverse=True)

        return success(
            LogListResponse(
                total=len(all_log_entries),
                page=page,
                size=size,
                items=all_log_entries
            ), message=Messages.QUERY_SUCCESS
        )

    except DoesNotExist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )
    except Exception as e:
        logger.error(f"获取任务日志失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取任务日志失败"
        )


@router.get("/metrics")
async def get_log_metrics(
        current_user=Depends(get_current_user)
):
    """获取简单的日志统计指标"""
    try:
        # 获取用户的所有任务ID
        task_ids = await scheduler_service.get_user_task_ids(current_user.user_id)

        if not task_ids:
            return success({
                "total_log_files": 0,
                "total_executions": 0
            }, message=Messages.QUERY_SUCCESS)

        # 获取用户任务的执行记录
        executions = await scheduler_service.get_task_executions_by_task_ids(task_ids)
        total_log_files = 0

        for execution in executions:
            # 统计日志文件
            if execution.log_file_path and os.path.exists(execution.log_file_path):
                total_log_files += 1
            if execution.error_log_path and os.path.exists(execution.error_log_path):
                total_log_files += 1

        return success({
                "total_log_files": total_log_files,
                "total_executions": len(executions)
            }, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取日志指标失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取日志指标失败"
        )


@router.get("/performance/stats")
async def get_performance_stats(
        current_user=Depends(get_current_user)
):
    """获取日志系统性能统计"""
    try:
        from src.services.logs.log_performance_service import log_performance_monitor

        # 获取性能摘要
        performance_summary = log_performance_monitor.get_performance_summary()

        # 获取WebSocket统计
        websocket_stats = websocket_manager.get_stats()

        return success({
                "log_performance": performance_summary,
                "websocket_performance": websocket_stats,
                "error_statistics": error_handler.get_error_stats(),
                "timestamp": datetime.now().isoformat()
            }, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        error_id = error_handler.log_error(e, {
            "endpoint": "get_performance_stats",
            "user_id": current_user.user_id
        })

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取性能统计失败 (错误ID: {error_id})"
        )


@router.get("/performance/slow-operations")
async def get_slow_operations(
        threshold: float = Query(1.0, ge=0.1, le=10.0),
        limit: int = Query(10, ge=1, le=100),
        current_user=Depends(get_current_user)
):
    """获取慢操作列表"""
    try:
        from src.services.logs.log_performance_service import log_performance_monitor

        slow_operations = log_performance_monitor.get_slow_operations(threshold, limit)

        return success({
                "threshold_seconds": threshold,
                "slow_operations": slow_operations,
                "count": len(slow_operations)
            }, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        error_id = error_handler.log_error(e, {
            "endpoint": "get_slow_operations",
            "user_id": current_user.user_id,
            "threshold": threshold
        })

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取慢操作列表失败 (错误ID: {error_id})"
        )


@router.get("/analytics/daily")
async def get_daily_analytics(
        days: int = Query(7, ge=1, le=30),
        current_user=Depends(get_current_user)
):
    """获取日志系统日统计分析"""
    try:
        from src.services.logs.log_performance_service import log_statistics_service

        daily_stats = log_statistics_service.get_daily_statistics(days)
        user_stats = log_statistics_service.get_user_statistics()

        return success({
                "daily_statistics": daily_stats,
                "user_statistics": user_stats,
                "analysis_period_days": days
            }, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        error_id = error_handler.log_error(e, {
            "endpoint": "get_daily_analytics",
            "user_id": current_user.user_id,
            "days": days
        })

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取日统计失败 (错误ID: {error_id})"
        )
