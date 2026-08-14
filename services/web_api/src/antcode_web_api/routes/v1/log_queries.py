"""Structured TaskLog filtering and bounded pagination helpers."""

from __future__ import annotations

from antcode_core.common.log_limits import MEBIBYTE
from antcode_core.domain.models import TaskLog
from antcode_core.domain.schemas.logs import LogEntry, LogLevel, LogListResponse, LogType
from fastapi import HTTPException, status
from tortoise.expressions import Q

MAX_LOG_PAGE_ENTRIES = 32
MAX_LOG_PAGE_BYTES = 8 * MEBIBYTE


def filter_log_query(query, *, log_type, level, search):
    if log_type:
        query = query.filter(log_type__iexact=log_type.value)
    if level:
        query = query.filter(_effective_level_filter(level))
    if search:
        query = query.filter(content__icontains=search)
    return query


def _effective_level_filter(level: LogLevel) -> Q:
    """Filter by the API-visible level, where every stderr row is ERROR."""
    stderr = Q(log_type__iexact=LogType.STDERR.value)
    if level == LogLevel.ERROR:
        return stderr | Q(level=LogLevel.ERROR.value)
    return ~stderr & Q(level=level.value)


def _stored_log_entry(row: TaskLog, task_id: str) -> LogEntry:
    try:
        log_type = LogType(str(row.log_type).lower())
        level = LogLevel.ERROR if log_type == LogType.STDERR else LogLevel(row.level)
    except ValueError as exc:
        raise RuntimeError(f"task_logs 枚举值非法: id={row.id} level={row.level!r} log_type={row.log_type!r}") from exc
    return LogEntry(
        id=row.id,
        timestamp=row.timestamp,
        level=level,
        log_type=log_type,
        run_id=row.run_id,
        task_id=task_id,
        message=row.content,
        source=row.source,
        line_number=row.sequence,
    )


async def paginate_log_query(query, *, page: int, size: int, task_id: str) -> LogListResponse:
    total = await query.count()
    rows = await query.order_by("-id").offset((page - 1) * size).limit(size)
    items = [_stored_log_entry(row, task_id) for row in rows]
    response = LogListResponse(total=total, page=page, size=size, items=items)
    if len(response.model_dump_json().encode("utf-8")) > MAX_LOG_PAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="日志分页响应超过 8 MiB 字节上限")
    return response


__all__ = [
    "MAX_LOG_PAGE_BYTES",
    "MAX_LOG_PAGE_ENTRIES",
    "filter_log_query",
    "paginate_log_query",
]
