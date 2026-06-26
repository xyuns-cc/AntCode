"""WebSocket日志流接口"""

import contextlib
import secrets
from typing import Any

from antcode_core.common.config import settings
from antcode_core.common.security.auth import TokenData, get_current_user, jwt_auth
from antcode_core.domain.models import User
from antcode_core.domain.schemas.common import BaseResponse
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger

from antcode_web_api.response import Messages, success
from antcode_web_api.websockets.websocket_log_service import websocket_log_service

router = APIRouter()

_WS_TICKET_TTL_SECONDS = 60
_WS_TICKET_PREFIX = "ws_ticket:"


async def _ensure_authenticated_user(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或会话已失效")
    return user


@router.post("/ws-ticket", response_model=BaseResponse[dict])
async def issue_ws_ticket(current_user: TokenData = Depends(get_current_user)):
    """签发一次性 WebSocket 票据（60s TTL）

    T2: WebSocket 连接不再直接传 JWT。前端用此票据在 60s 内换取 WS 接入；
    一次性消耗，避免 token 出现在 URL/access log 中泄露。
    """
    if not settings.REDIS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WebSocket 票据签发依赖 Redis，请检查后端配置",
        )
    from antcode_core.infrastructure.redis import get_redis_client

    ticket = secrets.token_urlsafe(32)
    redis = await get_redis_client()
    stored = await redis.set(
        f"{_WS_TICKET_PREFIX}{ticket}",
        str(current_user.user_id),
        nx=True,
        ex=_WS_TICKET_TTL_SECONDS,
    )
    if not stored:
        # 极小概率碰撞，重试一次
        ticket = secrets.token_urlsafe(32)
        await redis.set(
            f"{_WS_TICKET_PREFIX}{ticket}",
            str(current_user.user_id),
            ex=_WS_TICKET_TTL_SECONDS,
        )
    return success(
        {"ticket": ticket, "ttl": _WS_TICKET_TTL_SECONDS},
        message=Messages.CREATED_SUCCESS,
    )


async def _resolve_ws_token(ticket: str | None, token: str | None) -> str:
    """从 ticket 或 token 解析 JWT；ticket 优先并一次性消耗。

    向后兼容旧的 `?token=<JWT>` 方式（标记为 deprecated，由前端尽快迁移）。
    """
    if ticket:
        if not settings.REDIS_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="票据校验依赖 Redis",
            )
        from antcode_core.infrastructure.redis import get_redis_client

        redis = await get_redis_client()
        key = f"{_WS_TICKET_PREFIX}{ticket}"
        raw = await redis.get(key)
        if not raw:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据无效或已过期")
        await redis.delete(key)  # 一次性
        user_id = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        # 重新签发短时 JWT 喂给下游 connect()，避免改动 websocket_log_service 内部签名校验
        user = await User.get_or_none(id=int(user_id))
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
        return jwt_auth.create_access_token(
            user_id=user.id,
            username=user.username,
            is_admin=bool(getattr(user, "is_admin", False)),
            role=getattr(user, "role", "user") or "user",
        )
    if token:
        logger.warning("WebSocket 使用 token query 参数（deprecated），请改用 /ws-ticket 流程")
        return token
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 ticket 或 token")


@router.websocket("/runs/{run_id}/logs")
async def websocket_logs_endpoint(
    websocket: WebSocket,
    run_id: str,
    ticket: str | None = Query(None, description="一次性票据，优先使用"),
    token: str | None = Query(None, description="（deprecated）JWT，兼容旧前端"),
):
    logger.info(f"WebSocket 连接请求: run_id={run_id}")

    try:
        resolved_token = await _resolve_ws_token(ticket, token)
    except HTTPException as exc:
        with contextlib.suppress(Exception):
            await websocket.accept()
            await websocket.close(code=4401, reason=exc.detail)
        return

    try:
        await websocket_log_service.connect(websocket, run_id, resolved_token)
    except WebSocketDisconnect:
        logger.info(f"WebSocket 客户端断开连接: {run_id}")
    except Exception:
        logger.exception("WebSocket 处理失败: run_id={}", run_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=4000, reason="Internal error")


@router.get("/stats", response_model=BaseResponse[dict[str, Any]])
async def get_websocket_stats(current_user: TokenData = Depends(get_current_user)):
    await _ensure_authenticated_user(current_user)
    try:
        from antcode_web_api.websockets.websocket_connection_manager import (
            websocket_manager,
        )

        stats = websocket_manager.get_stats()
        return success(stats, message="查询成功")
    except Exception as e:
        logger.error(f"获取 WebSocket 统计信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取统计信息失败")


@router.post("/cleanup", response_model=BaseResponse[dict[str, Any]])
async def cleanup_inactive_connections(current_user: TokenData = Depends(get_current_user)):
    user = await _ensure_authenticated_user(current_user)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    try:
        from antcode_web_api.websockets.websocket_connection_manager import (
            websocket_manager,
        )

        await websocket_manager.cleanup_inactive_connections()
        return success({"cleaned": True}, message="清理完成")
    except Exception as e:
        logger.error(f"清理连接失败: {e}")
        raise HTTPException(status_code=500, detail="清理失败")
