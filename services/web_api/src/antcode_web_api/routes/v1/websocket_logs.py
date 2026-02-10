"""WebSocket日志流接口"""

import contextlib

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger

from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User
from antcode_web_api.websockets.websocket_log_service import websocket_log_service

router = APIRouter()


async def _ensure_authenticated_user(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或会话已失效")
    return user


@router.websocket("/runs/{run_id}/logs")
async def websocket_logs_endpoint(
    websocket: WebSocket,
    run_id: str,
    token: str = Query(...),
):
    logger.info(f"WebSocket 连接请求: run_id={run_id}")

    try:
        await websocket_log_service.connect(websocket, run_id, token)
    except WebSocketDisconnect:
        logger.info(f"WebSocket 客户端断开连接: {run_id}")
    except Exception:
        logger.exception("WebSocket 处理失败: run_id={}", run_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=4000, reason="Internal error")


@router.get("/stats")
async def get_websocket_stats(current_user: TokenData = Depends(get_current_user)):
    await _ensure_authenticated_user(current_user)
    try:
        from antcode_web_api.websockets.websocket_connection_manager import (
            websocket_manager,
        )

        stats = websocket_manager.get_stats()
        return {"success": True, "code": 200, "message": "Success", "data": stats}
    except Exception as e:
        logger.error(f"获取 WebSocket 统计信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取统计信息失败")


@router.post("/cleanup")
async def cleanup_inactive_connections(current_user: TokenData = Depends(get_current_user)):
    user = await _ensure_authenticated_user(current_user)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    try:
        from antcode_web_api.websockets.websocket_connection_manager import (
            websocket_manager,
        )

        await websocket_manager.cleanup_inactive_connections()
        return {
            "success": True,
            "code": 200,
            "message": "Cleanup complete",
            "data": {"cleaned": True},
        }
    except Exception as e:
        logger.error(f"清理连接失败: {e}")
        raise HTTPException(status_code=500, detail="清理失败")
