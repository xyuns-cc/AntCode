"""WebSocket日志流接口"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from loguru import logger

from src.services.websockets.websocket_connection_manager import websocket_manager
from src.services.websockets.websocket_log_service import websocket_log_service

router = APIRouter()


@router.websocket("/executions/{execution_id}/logs")
async def websocket_logs_endpoint(websocket, execution_id, token=Query(...)):
    logger.info(f"WebSocket connection request: execution_id={execution_id}")
    
    try:
        await websocket_log_service.connect(websocket, execution_id, token)
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {execution_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close(code=4000, reason="Internal error")
        except:
            pass


@router.get("/stats")
async def get_websocket_stats():
    try:
        stats = websocket_manager.get_stats()
        return {"success": True, "code": 200, "message": "Success", "data": stats}
    except Exception as e:
        logger.error(f"Failed to get WebSocket stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stats")


@router.post("/cleanup")
async def cleanup_inactive_connections():
    try:
        await websocket_manager.cleanup_inactive_connections()
        return {"success": True, "code": 200, "message": "Cleanup complete", "data": {"cleaned": True}}
    except Exception as e:
        logger.error(f"Failed to cleanup connections: {e}")
        raise HTTPException(status_code=500, detail="Cleanup failed")
