"""SSE 实时日志流接口（挂载于 /api/v1/logs 前缀）。"""

from __future__ import annotations

import secrets
from typing import Any

from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User, UserSession
from antcode_core.domain.schemas.common import BaseResponse
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger

from antcode_web_api.deps import CurrentAdminUser
from antcode_web_api.response import Messages, success
from antcode_web_api.streams.log_stream_service import (
    log_stream_service,
    verify_execution_access,
)
from antcode_web_api.streams.run_stream_broker import (
    StreamLimitExceededError,
    run_stream_broker,
)

router = APIRouter()

_STREAM_TICKET_TTL_SECONDS = 60
_STREAM_TICKET_PREFIX = "sse_ticket:"


@router.post("/stream-ticket", response_model=BaseResponse[dict])
async def issue_stream_ticket(current_user: TokenData = Depends(get_current_user)):
    """签发一次性日志流票据（60s TTL）

    原生 EventSource 无法携带 Authorization 头，前端用此票据在 60s 内建立
    SSE 连接；一次性消耗（getdel），避免 JWT 出现在 URL / access log 中泄露。
    """
    from antcode_core.infrastructure.redis import get_redis_client

    ticket = secrets.token_urlsafe(32)
    redis = await get_redis_client()
    # 票据携带签发时所属的会话 jti（P1-09），使日志流绑定服务端会话：
    # revoke_all_sessions / 登出后，流内的周期重校验能据此终止存活连接。
    ticket_value = f"{current_user.user_id}:{current_user.session_jti or ''}"
    stored = await redis.set(
        f"{_STREAM_TICKET_PREFIX}{ticket}",
        ticket_value,
        nx=True,
        ex=_STREAM_TICKET_TTL_SECONDS,
    )
    if not stored:
        # 极小概率碰撞，重试一次
        ticket = secrets.token_urlsafe(32)
        await redis.set(
            f"{_STREAM_TICKET_PREFIX}{ticket}",
            ticket_value,
            ex=_STREAM_TICKET_TTL_SECONDS,
        )
    return success(
        {"ticket": ticket, "ttl": _STREAM_TICKET_TTL_SECONDS},
        message=Messages.CREATED_SUCCESS,
    )


async def resolve_stream_ticket(ticket: str | None) -> tuple[User, str]:
    """原子消费一次性 ticket，返回 (用户, session_jti)。

    与原 WebSocket ticket 不同，SSE 的校验在同一 HTTP 请求内完成，
    不再换发中间 JWT。
    """
    if not ticket:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少一次性 ticket")
    from antcode_core.infrastructure.redis import get_redis_client

    redis = await get_redis_client()
    raw = await redis.getdel(f"{_STREAM_TICKET_PREFIX}{ticket}")
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据无效或已过期")
    value = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    return await _resolve_ticket_session(value)


async def _resolve_ticket_session(value: str) -> tuple[User, str]:
    user_id_text, separator, session_jti = value.partition(":")
    if not separator or not session_jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据关联会话已失效")
    try:
        user_id = int(user_id_text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据关联会话已失效") from exc
    session = await UserSession.filter(
        jti=session_jti,
        user_id=user_id,
        revoked_at__isnull=True,
    ).first()
    user = await User.get_or_none(id=user_id)
    if session is None or user is None or not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据关联会话已失效")
    return user, session_jti


@router.get("/runs/{run_id}/stream")
async def stream_run_logs(
    run_id: str,
    ticket: str | None = Query(None, description="一次性票据"),
):
    """SSE 实时日志流。

    帧序列：run_status → historical_logs_start → log_line* →
    historical_logs_end|no_historical_logs → 实时帧；15s 无数据发 ping。
    鉴权/权限/容量问题在流开始前以标准 HTTP 状态码返回（401/403/404/429）。
    """
    logger.info(f"SSE 日志流连接请求: run_id={run_id}")
    user, session_jti = await resolve_stream_ticket(ticket)
    execution = await verify_execution_access(run_id, user)
    try:
        run_stream_broker.ensure_capacity(run_id, user.id)
    except StreamLimitExceededError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    return StreamingResponse(
        log_stream_service.stream(run_id, execution, user_id=user.id, session_jti=session_jti),
        media_type="text/event-stream",
        headers={
            # no-transform: 阻止中间层（代理/CDN）压缩或改写事件流
            "Cache-Control": "no-cache, no-transform",
            # nginx 对带此头的响应自动关闭 proxy_buffering（专用 location 之外的兜底）
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream/stats", response_model=BaseResponse[dict[str, Any]])
async def get_stream_stats(current_user: CurrentAdminUser):
    """日志流订阅统计（管理员）。"""
    return success(run_stream_broker.stats(), message="查询成功")
