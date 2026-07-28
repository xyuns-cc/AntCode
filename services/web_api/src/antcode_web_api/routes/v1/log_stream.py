"""SSE 实时日志流接口（挂载于 /api/v1/logs 前缀）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User, UserSession
from antcode_core.domain.schemas.common import BaseResponse
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger

from antcode_web_api.deps import CurrentAdminUser
from antcode_web_api.response import Messages, success
from antcode_web_api.streams.ingest_event_id import LogStreamCursor, parse_log_stream_cursor
from antcode_web_api.streams.log_stream_access import session_not_expired
from antcode_web_api.streams.log_stream_service import (
    log_stream_service,
    verify_execution_access,
)
from antcode_web_api.streams.log_stream_ticket import (
    STREAM_TICKET_TTL_SECONDS,
    InvalidStreamTicketError,
    StreamTicketClaims,
    consume_stream_ticket,
    create_stream_ticket,
)
from antcode_web_api.streams.run_stream_broker import (
    StreamLimitExceededError,
    run_stream_broker,
)

router = APIRouter()


@router.post("/stream-ticket", response_model=BaseResponse[dict])
async def issue_stream_ticket(
    run_id: str = Query(..., min_length=1, description="执行记录 ID"),
    current_user: TokenData = Depends(get_current_user),
):
    """签发一次性日志流票据（60s TTL）

    原生 EventSource 无法携带 Authorization 头，前端用此票据在 60s 内建立
    SSE 连接；一次性消耗（getdel），避免 JWT 出现在 URL / access log 中泄露。
    """
    from antcode_core.infrastructure.redis import get_redis_client

    user = await _load_active_ticket_user(current_user.user_id)
    await verify_execution_access(run_id, user)
    await _ensure_stream_capacity(run_id, user.id)
    session_jti = current_user.session_jti
    if not session_jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前会话无效")
    redis = await get_redis_client()
    claims = StreamTicketClaims(user_id=user.id, session_jti=session_jti, run_id=run_id)
    ticket = await create_stream_ticket(redis, claims)
    return success(
        {"ticket": ticket, "ttl": STREAM_TICKET_TTL_SECONDS},
        message=Messages.CREATED_SUCCESS,
    )


async def resolve_stream_ticket(ticket: str | None, *, run_id: str) -> tuple[User, str]:
    """原子消费一次性 ticket，返回 (用户, session_jti)。

    与原 WebSocket ticket 不同，SSE 的校验在同一 HTTP 请求内完成，
    不再换发中间 JWT。
    """
    if not ticket:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少一次性 ticket")
    from antcode_core.infrastructure.redis import get_redis_client

    redis = await get_redis_client()
    try:
        claims = await consume_stream_ticket(redis, ticket)
    except InvalidStreamTicketError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if claims.run_id != run_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据未绑定当前执行记录")
    return await _resolve_ticket_session(claims)


async def _resolve_ticket_session(claims: StreamTicketClaims) -> tuple[User, str]:
    now = datetime.now(UTC)
    session = await UserSession.filter(
        jti=claims.session_jti,
        user_id=claims.user_id,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).first()
    user = await User.get_or_none(id=claims.user_id)
    session_live = session is not None and session_not_expired(session)
    if not session_live or user is None or not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据关联会话已失效")
    return user, claims.session_jti


async def _load_active_ticket_user(user_id: int) -> User:
    user = await User.get_or_none(id=user_id)
    if user is None or not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    return user


async def _ensure_stream_capacity(run_id: str, user_id: int) -> None:
    try:
        await run_stream_broker.ensure_capacity(run_id, user_id)
    except StreamLimitExceededError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


@router.get("/runs/{run_id}/stream")
async def stream_run_logs(
    run_id: str,
    ticket: str | None = Query(None, description="一次性票据"),
    cursor: str | None = Query(None, description="断线恢复游标"),
):
    """SSE 实时日志流。

    帧序列：run_status → historical_logs_start → log_line* →
    historical_logs_end|no_historical_logs → 实时帧；15s 无数据发 ping。
    鉴权/权限/容量问题在流开始前以标准 HTTP 状态码返回（401/403/404/429）。
    """
    logger.info(f"SSE 日志流连接请求: run_id={run_id}")
    parsed_cursor = _parse_cursor(cursor)
    user, session_jti = await resolve_stream_ticket(ticket, run_id=run_id)
    execution = await verify_execution_access(run_id, user)
    await _ensure_stream_capacity(run_id, user.id)

    return StreamingResponse(
        log_stream_service.stream(
            run_id,
            execution,
            user_id=user.id,
            session_jti=session_jti,
            cursor=parsed_cursor,
        ),
        media_type="text/event-stream",
        headers={
            # no-transform: 阻止中间层（代理/CDN）压缩或改写事件流
            "Cache-Control": "no-cache, no-transform",
            # nginx 对带此头的响应自动关闭 proxy_buffering（专用 location 之外的兜底）
            "X-Accel-Buffering": "no",
        },
    )


def _parse_cursor(cursor: str | None) -> LogStreamCursor | None:
    if cursor is None:
        return None
    try:
        return parse_log_stream_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/stream/stats", response_model=BaseResponse[dict[str, Any]])
async def get_stream_stats(current_user: CurrentAdminUser):
    """日志流订阅统计（管理员）。"""
    return success(await run_stream_broker.stats(), message="查询成功")
