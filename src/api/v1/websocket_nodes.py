"""
节点 WebSocket 通信端点

支持:
- 实时日志推送
- 心跳保活
- 任务分发
- 双向通信
"""
import asyncio
import time
from typing import Dict, Set, Any
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter(tags=["节点WebSocket"])


class NodeConnectionManager:
    """节点 WebSocket 连接管理器"""

    def __init__(self):
        # 活跃连接: {node_id: WebSocket}
        self._connections: Dict[str, WebSocket] = {}

        # 连接元数据: {node_id: {connected_at, machine_code, ...}}
        self._metadata: Dict[str, Dict[str, Any]] = {}

        # 锁
        self._lock = asyncio.Lock()

    async def connect(
        self,
        node_id: str,
        websocket: WebSocket,
        machine_code: str = None,
    ):
        """添加连接"""
        await websocket.accept()

        async with self._lock:
            # 关闭旧连接
            if node_id in self._connections:
                try:
                    await self._connections[node_id].close()
                except Exception:
                    pass

            self._connections[node_id] = websocket
            self._metadata[node_id] = {
                "connected_at": datetime.now(),
                "machine_code": machine_code,
                "last_heartbeat": datetime.now(),
                "messages_received": 0,
                "messages_sent": 0,
            }

        logger.info(f"节点 WebSocket 连接: {node_id}")

    async def disconnect(self, node_id: str):
        """移除连接"""
        async with self._lock:
            self._connections.pop(node_id, None)
            self._metadata.pop(node_id, None)

        logger.info(f"节点 WebSocket 断开: {node_id}")

    async def send_to_node(self, node_id: str, message: Dict):
        """发送消息给指定节点"""
        websocket = self._connections.get(node_id)
        if not websocket:
            return False

        try:
            await websocket.send_json(message)

            if node_id in self._metadata:
                self._metadata[node_id]["messages_sent"] += 1

            return True
        except Exception as e:
            logger.warning(f"发送消息失败 [{node_id}]: {e}")
            return False

    async def broadcast(self, message: Dict, exclude: Set[str] = None):
        """广播消息给所有节点"""
        exclude = exclude or set()

        for node_id in list(self._connections.keys()):
            if node_id not in exclude:
                await self.send_to_node(node_id, message)

    def get_connected_nodes(self) -> list:
        """获取已连接的节点列表"""
        return list(self._connections.keys())

    def get_connection_stats(self) -> Dict[str, Any]:
        """获取连接统计"""
        return {
            "total_connections": len(self._connections),
            "nodes": {
                node_id: {
                    "connected_at": meta["connected_at"].isoformat(),
                    "last_heartbeat": meta["last_heartbeat"].isoformat(),
                    "messages_received": meta["messages_received"],
                    "messages_sent": meta["messages_sent"],
                }
                for node_id, meta in self._metadata.items()
            }
        }

    def is_connected(self, node_id: str) -> bool:
        """检查节点是否已连接"""
        return node_id in self._connections


# 全局连接管理器
node_connection_manager = NodeConnectionManager()


@router.websocket("/ws/{node_id}")
async def node_websocket_endpoint(
    websocket: WebSocket,
    node_id: str,
):
    """
    节点 WebSocket 端点
    
    消息格式:
    {
        "type": "heartbeat|log|log_batch|task_status|metrics",
        "timestamp": 1234567890.123,
        "...": "其他字段"
    }
    """
    from src.services.nodes import node_service

    # 获取认证信息
    api_key = websocket.headers.get("authorization", "").replace("Bearer ", "")
    machine_code = websocket.headers.get("x-machine-code", "")

    # 验证节点
    node = await node_service.get_node_by_id(node_id)
    if not node:
        await websocket.close(code=4004, reason="Node not found")
        return

    if node.api_key != api_key:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 建立连接
    await node_connection_manager.connect(node_id, websocket, machine_code)

    try:
        while True:
            # 接收消息
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # 更新统计
            if node_id in node_connection_manager._metadata:
                node_connection_manager._metadata[node_id]["messages_received"] += 1

            # 处理不同类型的消息
            if msg_type == "heartbeat":
                await handle_heartbeat(node_id, node, data)

            elif msg_type == "log":
                await handle_log(data)

            elif msg_type == "log_batch":
                await handle_log_batch(data)

            elif msg_type == "task_status":
                await handle_task_status(data)

            elif msg_type == "metrics":
                await handle_metrics(node_id, node, data)

            elif msg_type == "pong":
                # 更新心跳时间
                if node_id in node_connection_manager._metadata:
                    node_connection_manager._metadata[node_id]["last_heartbeat"] = datetime.now()

    except WebSocketDisconnect:
        logger.info(f"节点 WebSocket 主动断开: {node_id}")
    except Exception as e:
        logger.error(f"节点 WebSocket 异常 [{node_id}]: {e}")
    finally:
        await node_connection_manager.disconnect(node_id)

        # 更新节点状态
        try:
            node.status = "offline"
            await node.save()
        except Exception:
            pass


async def handle_heartbeat(node_id: str, node, data: Dict):
    """处理心跳消息"""
    from src.services.nodes import node_service

    metrics = data.get("metrics", {})
    os_info = data.get("os_info", {})

    # 更新节点状态
    await node_service.heartbeat(
        node_id=node_id,
        api_key=node.api_key,
        status_value="online",
        metrics=metrics,
        os_type=os_info.get("os_type"),
        os_version=os_info.get("os_version"),
        python_version=os_info.get("python_version"),
        machine_arch=os_info.get("machine_arch"),
    )

    # 更新元数据
    if node_id in node_connection_manager._metadata:
        node_connection_manager._metadata[node_id]["last_heartbeat"] = datetime.now()


async def handle_log(data: Dict):
    """处理单条日志"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    execution_id = data.get("execution_id")
    log_type = data.get("log_type", "stdout")
    content = data.get("content", "")

    if execution_id and content:
        await distributed_log_service.append_log(
            execution_id=execution_id,
            log_type=log_type,
            content=content,
        )


async def handle_log_batch(data: Dict):
    """处理批量日志"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    logs = data.get("logs", [])

    for log in logs:
        execution_id = log.get("execution_id")
        log_type = log.get("log_type", "stdout")
        content = log.get("content", "")

        if execution_id and content:
            await distributed_log_service.append_log(
                execution_id=execution_id,
                log_type=log_type,
                content=content,
            )


async def handle_task_status(data: Dict):
    """处理任务状态"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    execution_id = data.get("execution_id")
    status = data.get("status")
    exit_code = data.get("exit_code")
    error_message = data.get("error_message")

    if execution_id and status:
        await distributed_log_service.update_task_status(
            execution_id=execution_id,
            status=status,
            exit_code=exit_code,
            error_message=error_message,
        )


async def handle_metrics(node_id: str, node, data: Dict):
    """处理指标上报"""
    from src.services.nodes import node_service

    metrics = data.get("metrics", {})

    # 保存指标历史
    await node_service.save_metrics_history(node.id, metrics)


# ==================== 任务分发接口 ====================

async def dispatch_task_via_websocket(
    node_id: str,
    task_data: Dict,
) -> bool:
    """
    通过 WebSocket 分发任务到节点
    
    Args:
        node_id: 目标节点 ID
        task_data: 任务数据
    
    Returns:
        是否发送成功
    """
    message = {
        "type": "task_dispatch",
        "timestamp": time.time(),
        **task_data,
    }

    return await node_connection_manager.send_to_node(node_id, message)


async def cancel_task_via_websocket(
    node_id: str,
    task_id: str,
    execution_id: str,
) -> bool:
    """通过 WebSocket 取消节点上的任务"""
    message = {
        "type": "task_cancel",
        "timestamp": time.time(),
        "task_id": task_id,
        "execution_id": execution_id,
    }

    return await node_connection_manager.send_to_node(node_id, message)


# ==================== HTTP API ====================

from fastapi import Depends
from src.core.security.auth import get_current_user, TokenData
from src.core.response import success, BaseResponse


@router.get(
    "/ws/stats",
    response_model=BaseResponse[dict],
    summary="获取 WebSocket 连接统计",
)
async def get_websocket_stats(
    current_user: TokenData = Depends(get_current_user),
):
    """获取所有节点 WebSocket 连接的统计信息"""
    stats = node_connection_manager.get_connection_stats()
    return success(stats)


@router.get(
    "/ws/connected",
    response_model=BaseResponse[list],
    summary="获取已连接节点列表",
)
async def get_connected_nodes(
    current_user: TokenData = Depends(get_current_user),
):
    """获取通过 WebSocket 连接的节点列表"""
    nodes = node_connection_manager.get_connected_nodes()
    return success(nodes)


@router.post(
    "/ws/{node_id}/ping",
    response_model=BaseResponse[dict],
    summary="Ping 指定节点",
)
async def ping_node(
    node_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """通过 WebSocket 向节点发送 Ping"""
    if not node_connection_manager.is_connected(node_id):
        return success({"connected": False, "message": "节点未通过 WebSocket 连接"})

    sent = await node_connection_manager.send_to_node(node_id, {
        "type": "ping",
        "timestamp": time.time(),
    })

    return success({"sent": sent, "node_id": node_id})

