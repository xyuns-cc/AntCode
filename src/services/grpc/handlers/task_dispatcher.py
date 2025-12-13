"""
任务分发器

通过 gRPC 流向节点分发任务。
**Validates: Requirements 5.1, 5.2**
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from src.grpc_generated import MasterMessage, TaskDispatch, TaskAck
from src.services.grpc.dispatcher import MessageHandler, NodeContext


class TaskDispatcher:
    """任务分发器
    
    负责通过 gRPC 流向节点分发任务，并处理任务确认。
    """
    
    # 任务确认超时时间（秒）
    ACK_TIMEOUT = 5.0
    
    def __init__(self):
        # 待确认的任务: {task_id: asyncio.Event}
        self._pending_acks: Dict[str, asyncio.Event] = {}
        # 任务确认结果: {task_id: TaskAck}
        self._ack_results: Dict[str, TaskAck] = {}
        self._lock = asyncio.Lock()
    
    async def dispatch_task(
        self,
        node_id: str,
        task_id: str,
        project_id: str,
        project_type: str = "spider",
        priority: int = 0,
        params: Optional[Dict[str, str]] = None,
        environment: Optional[Dict[str, str]] = None,
        timeout: int = 3600,
        download_url: str = "",
        file_hash: str = "",
        entry_point: str = "",
    ) -> Dict[str, Any]:
        """向节点分发任务
        
        Args:
            node_id: 目标节点 ID
            task_id: 任务 ID
            project_id: 项目 ID
            project_type: 项目类型
            priority: 优先级
            params: 任务参数
            environment: 环境变量
            timeout: 超时时间（秒）
            download_url: 项目下载 URL
            file_hash: 文件哈希
            entry_point: 入口点
            
        Returns:
            分发结果字典
        """
        try:
            # 获取节点连接
            from src.services.grpc.node_service_impl import get_node_service_impl
            
            node_service = get_node_service_impl()
            connection = await node_service.get_connection(node_id)
            
            if connection is None:
                logger.warning(f"节点未连接: {node_id}")
                return {
                    "success": False,
                    "error": f"节点未连接: {node_id}",
                }
            
            # 构建任务分发消息
            task_dispatch = TaskDispatch(
                task_id=task_id,
                project_id=project_id,
                project_type=project_type,
                priority=priority,
                timeout=timeout,
                download_url=download_url,
                file_hash=file_hash,
                entry_point=entry_point,
            )
            
            # 添加参数
            if params:
                for k, v in params.items():
                    task_dispatch.params[k] = str(v)
            
            # 添加环境变量
            if environment:
                for k, v in environment.items():
                    task_dispatch.environment[k] = str(v)
            
            # 构建主控消息
            master_message = MasterMessage(task_dispatch=task_dispatch)
            
            # 创建确认事件
            ack_event = asyncio.Event()
            async with self._lock:
                self._pending_acks[task_id] = ack_event
            
            try:
                # 发送消息
                sent = await connection.send(master_message)
                if not sent:
                    return {
                        "success": False,
                        "error": "发送消息失败",
                    }
                
                logger.info(f"任务已分发 - 节点: {node_id}, 任务: {task_id}")
                
                # 等待确认
                try:
                    await asyncio.wait_for(ack_event.wait(), timeout=self.ACK_TIMEOUT)
                    
                    # 获取确认结果
                    async with self._lock:
                        ack = self._ack_results.pop(task_id, None)
                    
                    if ack is None:
                        return {
                            "success": False,
                            "error": "未收到确认响应",
                        }
                    
                    if ack.accepted:
                        logger.info(f"任务已被接受 - 节点: {node_id}, 任务: {task_id}")
                        return {
                            "success": True,
                            "accepted": True,
                        }
                    else:
                        reason = ack.reason if ack.HasField("reason") else "未知原因"
                        logger.warning(f"任务被拒绝 - 节点: {node_id}, 任务: {task_id}, 原因: {reason}")
                        return {
                            "success": True,
                            "accepted": False,
                            "reason": reason,
                        }
                        
                except asyncio.TimeoutError:
                    logger.warning(f"任务确认超时 - 节点: {node_id}, 任务: {task_id}")
                    return {
                        "success": False,
                        "error": "确认超时",
                    }
                    
            finally:
                # 清理
                async with self._lock:
                    self._pending_acks.pop(task_id, None)
                    self._ack_results.pop(task_id, None)
                    
        except Exception as e:
            logger.error(f"任务分发异常 - 节点: {node_id}, 任务: {task_id}, 错误: {e}")
            return {
                "success": False,
                "error": str(e),
            }
    
    async def handle_task_ack(self, ack: TaskAck, context: NodeContext) -> None:
        """处理任务确认消息
        
        Args:
            ack: 任务确认消息
            context: 节点上下文
        """
        task_id = ack.task_id
        
        async with self._lock:
            if task_id in self._pending_acks:
                self._ack_results[task_id] = ack
                self._pending_acks[task_id].set()
                logger.debug(f"收到任务确认 - 节点: {context.node_id}, 任务: {task_id}")
            else:
                logger.warning(f"收到未知任务的确认 - 节点: {context.node_id}, 任务: {task_id}")


class TaskAckHandler(MessageHandler):
    """任务确认消息处理器"""
    
    def __init__(self, dispatcher: TaskDispatcher):
        self._dispatcher = dispatcher
    
    async def handle(self, message: TaskAck, context: NodeContext) -> Optional[Any]:
        """处理任务确认消息"""
        await self._dispatcher.handle_task_ack(message, context)
        return None


# 全局任务分发器实例
task_dispatcher = TaskDispatcher()

# 任务确认处理器
task_ack_handler = TaskAckHandler(task_dispatcher)
