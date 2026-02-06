"""WebSocket日志流接口"""

import contextlib

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from antcode_web_api.websockets.websocket_log_service import websocket_log_service

router = APIRouter()


@router.websocket("/executions/{execution_id}/logs")
async def websocket_logs_endpoint(
    websocket: WebSocket,
    execution_id: str,
    token: str = Query(...),
):
    logger.info(f"WebSocket 连接请求: execution_id={execution_id}")

    try:
        await websocket_log_service.connect(websocket, execution_id, token)
    except WebSocketDisconnect:
        logger.info(f"WebSocket 客户端断开连接: {execution_id}")
    except Exception:
        logger.exception("WebSocket 处理失败: execution_id={}", execution_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=4000, reason="Internal error")


@router.get("/stats")
async def get_websocket_stats():
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
async def cleanup_inactive_connections():
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
