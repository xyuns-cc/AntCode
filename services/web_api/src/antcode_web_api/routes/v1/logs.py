"""日志管理接口。"""

from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.logs.log_security_service import (
    error_handler,
    log_security_service,
)
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.logs import (
    LogEntry,
    LogFormat,
    LogLevel,
    LogListResponse,
    LogType,
    UnifiedLogResponse,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from tortoise.exceptions import DoesNotExist

from antcode_web_api.response import Messages, success

router = APIRouter()


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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


def _line_entries(
    lines: list[str],
    log_type: LogType,
    execution,
    run_id: str,
    search: str | None,
) -> list[LogEntry]:
    level = LogLevel.ERROR if log_type == LogType.STDERR else LogLevel.INFO
    timestamp = execution.start_time or execution.created_at or _utcnow_naive()
    return [
        LogEntry(
            id=index,
            timestamp=timestamp,
            level=level,
            log_type=log_type,
            run_id=run_id,
            task_id=str(execution.task_id),
            message=line.strip(),
            source="task_execution",
        )
        for index, line in enumerate(lines)
        if line.strip() and (not search or search.lower() in line.lower())
    ]


def _structured_entries(
    logs_data: dict[str, str],
    execution,
    run_id: str,
    log_type: LogType | None,
    search: str | None,
) -> list[LogEntry]:
    entries: list[LogEntry] = []
    if not log_type or log_type == LogType.STDOUT:
        entries.extend(
            _line_entries(logs_data.get("output", "").split("\n"), LogType.STDOUT, execution, run_id, search)
        )
    if not log_type or log_type == LogType.STDERR:
        entries.extend(_line_entries(logs_data.get("error", "").split("\n"), LogType.STDERR, execution, run_id, search))
    return entries


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
    except Exception as e:
        logger.error(f"获取原始日志失败: {e}")
        raise HTTPException(status_code=500, detail="获取日志失败")


async def _get_structured_log_response(run_id, execution, log_type, level, lines, search):
    try:
        normalized_log_type = _normalize_log_type(log_type)
        normalized_level = _normalize_log_level(level)
        logs_data = await task_log_service.get_execution_logs(run_id)
        entries = _structured_entries(logs_data, execution, run_id, normalized_log_type, search)
        if normalized_level:
            entries = [entry for entry in entries if entry.level == normalized_level]
        if lines:
            entries = entries[-lines:]
        data = LogListResponse(total=len(entries), page=1, size=len(entries), items=entries)
        return success(
            UnifiedLogResponse(
                run_id=run_id,
                format=LogFormat.STRUCTURED,
                log_type=normalized_log_type.value if normalized_log_type else "",
                structured_data=data,
            ),
            message=Messages.QUERY_SUCCESS,
        )
    except Exception as e:
        logger.error(f"获取结构化日志失败: {e}")
        raise HTTPException(status_code=500, detail="获取日志失败")


@router.get("/runs/{run_id}", response_model=BaseResponse[UnifiedLogResponse])
async def get_run_logs(
    run_id,
    format: LogFormat | str = Query(LogFormat.STRUCTURED),
    log_type: LogType | str | None = Query(None),
    level: LogLevel | str | None = Query(None),
    lines: int | None = Query(None, ge=1, le=10000),
    search: str | None = Query(None),
    current_user=Depends(get_current_user),
):
    try:
        normalized_format = _normalize_log_format(format)
        execution = await log_security_service.verify_log_access_permission(current_user, run_id, "read")
        if normalized_format == LogFormat.RAW:
            return await _get_raw_log_response(run_id, execution, log_type, lines)
        return await _get_structured_log_response(run_id, execution, log_type, level, lines, search)
    except HTTPException:
        raise
    except Exception as e:
        error_id = error_handler.log_error(e, {"endpoint": "get_run_logs", "run_id": run_id})
        raise HTTPException(status_code=500, detail=f"获取运行日志失败 (error_id: {error_id})")


@router.get("/runs/{run_id}/stdout", response_model=BaseResponse[UnifiedLogResponse])
async def get_stdout_logs(
    run_id,
    format: LogFormat | str = Query(LogFormat.RAW),
    lines: int | None = Query(None, ge=1, le=10000),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(run_id, format, LogType.STDOUT, None, lines, None, current_user)


@router.get("/runs/{run_id}/stderr", response_model=BaseResponse[UnifiedLogResponse])
async def get_stderr_logs(
    run_id,
    format: LogFormat | str = Query(LogFormat.RAW),
    lines: int | None = Query(None, ge=1, le=10000),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(run_id, format, LogType.STDERR, None, lines, None, current_user)


@router.get("/runs/{run_id}/errors", response_model=BaseResponse[UnifiedLogResponse])
async def get_error_logs(
    run_id,
    format: LogFormat | str = Query(LogFormat.STRUCTURED),
    lines: int | None = Query(None, ge=1, le=10000),
    search: str | None = Query(None),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(run_id, format, None, LogLevel.ERROR, lines, search, current_user)


@router.get("/runs/{run_id}/raw", response_model=BaseResponse[UnifiedLogResponse])
async def get_raw_logs(
    run_id,
    log_type: LogType | str | None = Query(None),
    lines: int | None = Query(None, ge=1, le=10000),
    current_user=Depends(get_current_user),
):
    return await get_run_logs(run_id, LogFormat.RAW, log_type, None, lines, None, current_user)


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
    current_user=Depends(get_current_user),
):
    try:
        result = await scheduler_service.get_task_executions(
            task_id=task_id,
            user_id=current_user.user_id,
            start_date=start_time,
            end_date=end_time,
            page=page,
            size=size,
        )
        entries = []
        normalized_type = _normalize_log_type(log_type)
        normalized_level = _normalize_log_level(level)
        for execution in result["executions"]:
            logs_data = await task_log_service.get_execution_logs(execution.run_id)
            entries.extend(_structured_entries(logs_data, execution, execution.run_id, normalized_type, search))
        if normalized_level:
            entries = [entry for entry in entries if entry.level == normalized_level]
        entries.sort(key=lambda item: item.timestamp, reverse=True)
        return success(LogListResponse(total=len(entries), page=page, size=size, items=entries))
    except DoesNotExist:
        raise HTTPException(status_code=404, detail="任务不存在")


@router.get("/metrics", response_model=BaseResponse[dict[str, Any]])
async def get_log_metrics(current_user: TokenData = Depends(get_current_user)):
    task_ids = await scheduler_service.get_user_task_ids(current_user.user_id)
    if not task_ids:
        return success({"total_log_rows": 0, "total_executions": 0})
    executions = await scheduler_service.get_task_executions_by_task_ids(task_ids)
    return success({"total_log_rows": 0, "total_executions": len(executions)})
