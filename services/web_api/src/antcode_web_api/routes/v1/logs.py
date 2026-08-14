"""日志管理接口。"""

from datetime import datetime
from typing import Annotated, Any

from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.logs.log_security_service import (
    error_handler,
    log_security_service,
)
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import Task, TaskLog, TaskRun
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.logs import (
    LogFormat,
    LogLevel,
    LogListResponse,
    LogType,
    UnifiedLogResponse,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from tortoise.expressions import Subquery

from antcode_web_api.response import Messages, success
from antcode_web_api.routes.v1.log_queries import (
    MAX_LOG_PAGE_ENTRIES,
    filter_log_query,
    paginate_log_query,
)

router = APIRouter()


def _normalize_log_type(log_type: LogType | str | None) -> LogType | None:
    if log_type is None:
        return None
    if isinstance(log_type, LogType):
        return log_type
    try:
        return LogType(log_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的日志类型") from exc


def _normalize_log_format(format_value: LogFormat | str) -> LogFormat:
    if isinstance(format_value, LogFormat):
        return format_value
    try:
        return LogFormat(format_value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的日志格式") from exc


def _normalize_log_level(level: LogLevel | str | None) -> LogLevel | None:
    if level is None:
        return None
    if isinstance(level, LogLevel):
        return level
    try:
        return LogLevel(level)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的日志级别") from exc


def _select_raw_content(logs_data: dict[str, str], log_type: LogType | None) -> str:
    stdout_content = logs_data.get("output", "")
    stderr_content = logs_data.get("error", "")
    if log_type == LogType.STDOUT:
        return stdout_content
    if log_type == LogType.STDERR:
        return stderr_content
    if stdout_content and stderr_content:
        return f"=== STDOUT ===\n{stdout_content}\n\n=== STDERR ===\n{stderr_content}"
    return stdout_content or stderr_content


def _tail_content(content: str, lines: int | None) -> str:
    if not lines or not content:
        return content
    return "\n".join(content.split("\n")[-lines:])


async def _get_raw_log_response(run_id, execution, log_type, lines):
    try:
        normalized_log_type = _normalize_log_type(log_type)
        logs_data = await task_log_service.get_execution_logs(run_id)
        content = _tail_content(_select_raw_content(logs_data, normalized_log_type), lines)
        return success(
            UnifiedLogResponse(
                run_id=run_id,
                format=LogFormat.RAW,
                log_type=normalized_log_type.value if normalized_log_type else "mixed",
                raw_content=content,
                lines_count=len(content.split("\n")) if content else 0,
            ),
            message=Messages.QUERY_SUCCESS,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取原始日志失败: {e}")
        raise HTTPException(status_code=500, detail="获取日志失败") from e


async def _get_structured_log_response(run_id, execution, *, log_type, level, search, page, size):
    try:
        normalized_log_type = _normalize_log_type(log_type)
        normalized_level = _normalize_log_level(level)
        query = filter_log_query(
            TaskLog.filter(run_id=run_id),
            log_type=normalized_log_type,
            level=normalized_level,
            search=search,
        )
        data = await paginate_log_query(query, page=page, size=size, task_id=str(execution.task_id))
        return success(
            UnifiedLogResponse(
                run_id=run_id,
                format=LogFormat.STRUCTURED,
                log_type=normalized_log_type.value if normalized_log_type else "",
                structured_data=data,
            ),
            message=Messages.QUERY_SUCCESS,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取结构化日志失败: {e}")
        raise HTTPException(status_code=500, detail="获取日志失败") from e


@router.get("/runs/{run_id}", response_model=BaseResponse[UnifiedLogResponse])
async def get_run_logs(
    run_id,
    *,
    format: LogFormat | str = Query(LogFormat.STRUCTURED),
    log_type: LogType | str | None = Query(None),
    level: LogLevel | str | None = Query(None),
    lines: Annotated[int | None, Query(ge=1, le=10000)] = None,
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(MAX_LOG_PAGE_ENTRIES, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    current_user=Depends(get_current_user),
):
    try:
        normalized_format = _normalize_log_format(format)
        if normalized_format == LogFormat.STRUCTURED and lines is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="结构化日志不支持 lines 参数，请使用 page 和 size 分页",
            )
        execution = await log_security_service.verify_log_access_permission(current_user, run_id, "read")
        if normalized_format == LogFormat.RAW:
            return await _get_raw_log_response(run_id, execution, log_type, lines)
        return await _get_structured_log_response(
            run_id,
            execution,
            log_type=log_type,
            level=level,
            search=search,
            page=page,
            size=size,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_id = error_handler.log_error(e, {"endpoint": "get_run_logs", "run_id": run_id})
        raise HTTPException(status_code=500, detail=f"获取运行日志失败 (error_id: {error_id})")


@router.get("/runs/{run_id}/stdout", response_model=BaseResponse[UnifiedLogResponse])
async def get_stdout_logs(
    run_id,
    *,
    format: LogFormat | str = Query(LogFormat.RAW),
    lines: Annotated[int | None, Query(ge=1, le=10000)] = None,
    page: int = Query(1, ge=1),
    size: int = Query(MAX_LOG_PAGE_ENTRIES, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(
        run_id,
        format=format,
        log_type=LogType.STDOUT,
        lines=lines,
        page=page,
        size=size,
        current_user=current_user,
    )


@router.get("/runs/{run_id}/stderr", response_model=BaseResponse[UnifiedLogResponse])
async def get_stderr_logs(
    run_id,
    *,
    format: LogFormat | str = Query(LogFormat.RAW),
    lines: Annotated[int | None, Query(ge=1, le=10000)] = None,
    page: int = Query(1, ge=1),
    size: int = Query(MAX_LOG_PAGE_ENTRIES, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(
        run_id,
        format=format,
        log_type=LogType.STDERR,
        lines=lines,
        page=page,
        size=size,
        current_user=current_user,
    )


@router.get("/runs/{run_id}/errors", response_model=BaseResponse[UnifiedLogResponse])
async def get_error_logs(
    run_id,
    *,
    format: LogFormat | str = Query(LogFormat.STRUCTURED),
    lines: Annotated[int | None, Query(ge=1, le=10000)] = None,
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(MAX_LOG_PAGE_ENTRIES, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(
        run_id,
        format=format,
        level=LogLevel.ERROR,
        lines=lines,
        search=search,
        page=page,
        size=size,
        current_user=current_user,
    )


@router.get("/runs/{run_id}/raw", response_model=BaseResponse[UnifiedLogResponse])
async def get_raw_logs(
    run_id,
    log_type: LogType | str | None = Query(None),
    lines: Annotated[int | None, Query(ge=1, le=10000)] = None,
    current_user=Depends(get_current_user),
):
    return await get_run_logs(
        run_id,
        format=LogFormat.RAW,
        log_type=log_type,
        lines=lines,
        current_user=current_user,
    )


@router.get("/tasks/{task_id}", response_model=BaseResponse[LogListResponse])
async def get_task_logs(
    task_id,
    page: int = Query(1, ge=1),
    size: int = Query(MAX_LOG_PAGE_ENTRIES, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    log_type: LogType | str | None = Query(None),
    level: LogLevel | str | None = Query(None),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    search: str | None = Query(None),
    current_user=Depends(get_current_user),
):
    try:
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=current_user.user_id, check_admin=True)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在或无权访问")
        normalized_type = _normalize_log_type(log_type)
        normalized_level = _normalize_log_level(level)
        run_ids = TaskRun.filter(task_id=task.id).values("run_id")
        query = TaskLog.filter(run_id__in=Subquery(run_ids))
        query = filter_log_query(query, log_type=normalized_type, level=normalized_level, search=search)
        if start_time:
            query = query.filter(timestamp__gte=start_time)
        if end_time:
            query = query.filter(timestamp__lte=end_time)
        data = await paginate_log_query(query, page=page, size=size, task_id=str(task.public_id))
        return success(data)
    except HTTPException:
        raise


@router.get("/metrics", response_model=BaseResponse[dict[str, Any]])
async def get_log_metrics(current_user: TokenData = Depends(get_current_user)):
    task_ids = await scheduler_service.get_user_task_ids(current_user.user_id)
    if not task_ids:
        return success({"total_log_rows": 0, "total_executions": 0})
    executions = await scheduler_service.get_task_executions_by_task_ids(task_ids)
    return success({"total_log_rows": 0, "total_executions": len(executions)})
