"""
WebSocket日志API路由
提供实时日志推送的WebSocket接口
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from loguru import logger

from src.services.websockets.websocket_log_service import websocket_log_service

router = APIRouter()


@router.websocket("/executions/{execution_id}/logs")
async def websocket_logs_endpoint(
    websocket: WebSocket,
    execution_id: str,
    token: str = Query(..., description="JWT认证令牌")
):
    """
    WebSocket实时日志推送接口
    
    连接地址: ws://localhost:8000/api/v1/ws/executions/{execution_id}/logs?token={jwt_token}
    
    Args:
        execution_id: 执行ID
        token: JWT认证令牌
    
    消息格式:
        服务端发送:
        - connected: 连接建立确认
        - log_line: 实时日志行
        - execution_status: 执行状态更新
        - historical_logs_start/end: 历史日志标记
        - no_historical_logs: 无历史日志
        - pong: 心跳响应
        - stats: 连接统计
        
        客户端发送:
        - ping: 心跳检测
        - get_stats: 获取连接统计
    """
    logger.info(f"🔗 新的WebSocket日志连接请求: 执行ID={execution_id}")
    
    try:
        # 委托给WebSocket日志服务处理
        await websocket_log_service.connect(websocket, execution_id, token)
        
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket客户端主动断开连接: {execution_id}")
        
    except Exception as e:
        logger.error(f"❌ WebSocket连接处理异常: {e}")
        try:
            await websocket.close(code=4000, reason="服务器内部错误")
        except:
            pass


@router.get("/stats")
async def get_websocket_stats():
    """获取WebSocket连接统计信息"""
    try:
        from src.services.websockets.websocket_connection_manager import websocket_manager
        stats = websocket_manager.get_stats()
        
        return {
            "success": True,
            "code": 200,
            "message": "获取成功",
            "data": stats
        }
        
    except Exception as e:
        logger.error(f"❌ 获取WebSocket统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取统计信息失败")


@router.post("/cleanup")
async def cleanup_inactive_connections():
    """清理不活跃的WebSocket连接"""
    try:
        from src.services.websockets.websocket_connection_manager import websocket_manager
        await websocket_manager.cleanup_inactive_connections()
        
        return {
            "success": True,
            "code": 200,
            "message": "清理完成",
            "data": {"cleaned": True}
        }
        
    except Exception as e:
        logger.error(f"❌ 清理WebSocket连接失败: {e}")
        raise HTTPException(status_code=500, detail="清理连接失败")