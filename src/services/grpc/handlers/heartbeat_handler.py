"""
心跳消息处理器

处理节点心跳消息，更新节点状态和指标。
**Validates: Requirements 2.4**
"""
from datetime import datetime
from typing import Any, Optional

from loguru import logger

from src.services.grpc.dispatcher import MessageHandler, NodeContext
from src.grpc_generated import Heartbeat


class HeartbeatHandler(MessageHandler):
    """心跳消息处理器
    
    处理节点发送的心跳消息，更新数据库中的节点状态和指标。
    """
    
    async def handle(self, message: Heartbeat, context: NodeContext) -> Optional[Any]:
        """处理心跳消息
        
        Args:
            message: 心跳消息
            context: 节点上下文
            
        Returns:
            None
        """
        try:
            # 更新上下文中的心跳时间
            context.update_heartbeat()
            
            # 提取心跳数据
            node_id = message.node_id or context.node_id
            status = message.status or "online"
            
            # 提取指标
            metrics = self._extract_metrics(message)
            
            # 提取操作系统信息
            os_info = self._extract_os_info(message)
            
            # 提取节点能力
            capabilities = dict(message.capabilities) if message.capabilities else None
            
            # 更新数据库
            success = await self._update_node_in_database(
                node_id=node_id,
                api_key=context.api_key,
                status=status,
                metrics=metrics,
                os_info=os_info,
                capabilities=capabilities,
            )
            
            if success:
                logger.debug(f"心跳处理成功 - 节点: {node_id}, 状态: {status}")
            else:
                logger.warning(f"心跳处理失败 - 节点: {node_id}")
            
            return None
            
        except Exception as e:
            logger.error(f"心跳处理异常 - 节点: {context.node_id}, 错误: {e}")
            raise
    
    def _extract_metrics(self, message: Heartbeat) -> Optional[dict]:
        """从心跳消息中提取指标"""
        if not message.HasField("metrics"):
            return None
        
        m = message.metrics
        return {
            "cpu": m.cpu,
            "memory": m.memory,
            "disk": m.disk,
            "runningTasks": m.running_tasks,
            "maxConcurrentTasks": m.max_concurrent_tasks,
            "taskCount": m.task_count,
        }
    
    def _extract_os_info(self, message: Heartbeat) -> dict:
        """从心跳消息中提取操作系统信息"""
        if not message.HasField("os_info"):
            return {}
        
        os = message.os_info
        return {
            "os_type": os.os_type,
            "os_version": os.os_version,
            "python_version": os.python_version,
            "machine_arch": os.machine_arch,
        }
    
    async def _update_node_in_database(
        self,
        node_id: str,
        api_key: str,
        status: str,
        metrics: Optional[dict],
        os_info: dict,
        capabilities: Optional[dict],
    ) -> bool:
        """更新数据库中的节点状态和指标
        
        Args:
            node_id: 节点 ID
            api_key: API 密钥
            status: 节点状态
            metrics: 节点指标
            os_info: 操作系统信息
            capabilities: 节点能力
            
        Returns:
            是否更新成功
        """
        try:
            from src.services.nodes import node_service
            from src.schemas.node import NodeMetrics
            
            # 构建 NodeMetrics 对象
            node_metrics = None
            if metrics:
                node_metrics = NodeMetrics(
                    cpu=metrics.get("cpu", 0),
                    memory=metrics.get("memory", 0),
                    disk=metrics.get("disk", 0),
                    runningTasks=metrics.get("runningTasks", 0),
                    maxConcurrentTasks=metrics.get("maxConcurrentTasks", 1),
                    taskCount=metrics.get("taskCount", 0),
                )
            
            # 调用节点服务更新心跳
            success = await node_service.heartbeat(
                node_id=node_id,
                api_key=api_key,
                status_value=status,
                metrics=node_metrics,
                os_type=os_info.get("os_type"),
                os_version=os_info.get("os_version"),
                python_version=os_info.get("python_version"),
                machine_arch=os_info.get("machine_arch"),
                capabilities=capabilities,
            )
            
            return success
            
        except ImportError:
            logger.warning("node_service 不可用，跳过数据库更新")
            return True
        except Exception as e:
            logger.error(f"更新节点数据库失败: {e}")
            return False


# 全局处理器实例
heartbeat_handler = HeartbeatHandler()
