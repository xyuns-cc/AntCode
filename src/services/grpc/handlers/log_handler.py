"""
日志消息处理器

处理节点上报的日志批次消息，持久化日志并广播到 WebSocket 客户端。
**Validates: Requirements 3.5**
"""
import gzip
from datetime import datetime
from typing import Any, List, Optional

from loguru import logger

from src.services.grpc.dispatcher import MessageHandler, NodeContext
from src.grpc_generated import LogBatch, LogEntry


class LogHandler(MessageHandler):
    """日志消息处理器
    
    处理节点发送的日志批次消息：
    1. 解压缩（如果已压缩）
    2. 持久化到数据库/文件
    3. 广播到 WebSocket 客户端
    """
    
    async def handle(self, message: LogBatch, context: NodeContext) -> Optional[Any]:
        """处理日志批次消息
        
        Args:
            message: 日志批次消息
            context: 节点上下文
            
        Returns:
            None
        """
        try:
            # 获取日志条目
            logs = self._extract_logs(message)
            
            if not logs:
                logger.debug(f"收到空日志批次 - 节点: {context.node_id}")
                return None
            
            logger.debug(f"收到日志批次 - 节点: {context.node_id}, 条数: {len(logs)}")
            
            # 持久化日志并广播
            await self._persist_and_broadcast_logs(logs, context)
            
            return None
            
        except Exception as e:
            logger.error(f"日志处理异常 - 节点: {context.node_id}, 错误: {e}")
            raise
    
    def _extract_logs(self, message: LogBatch) -> List[dict]:
        """从日志批次消息中提取日志条目
        
        Args:
            message: 日志批次消息
            
        Returns:
            日志条目列表
        """
        # 检查是否压缩
        if message.compressed and message.compressed_data:
            return self._decompress_logs(message.compressed_data)
        
        # 未压缩，直接提取
        logs = []
        for entry in message.logs:
            logs.append(self._log_entry_to_dict(entry))
        
        return logs
    
    def _decompress_logs(self, compressed_data: bytes) -> List[dict]:
        """解压缩日志数据
        
        Args:
            compressed_data: gzip 压缩的日志数据
            
        Returns:
            日志条目列表
        """
        try:
            import json
            decompressed = gzip.decompress(compressed_data)
            data = json.loads(decompressed.decode("utf-8"))
            
            if isinstance(data, list):
                return data
            return []
            
        except Exception as e:
            logger.error(f"解压缩日志失败: {e}")
            return []
    
    def _log_entry_to_dict(self, entry: LogEntry) -> dict:
        """将 LogEntry 转换为字典
        
        Args:
            entry: LogEntry protobuf 消息
            
        Returns:
            日志条目字典
        """
        timestamp = None
        if entry.HasField("timestamp"):
            timestamp = datetime.fromtimestamp(
                entry.timestamp.seconds + entry.timestamp.nanos / 1e9
            )
        
        return {
            "execution_id": entry.execution_id,
            "log_type": entry.log_type,
            "content": entry.content,
            "timestamp": timestamp,
        }
    
    async def _persist_and_broadcast_logs(
        self,
        logs: List[dict],
        context: NodeContext
    ) -> None:
        """持久化日志并广播到 WebSocket 客户端
        
        Args:
            logs: 日志条目列表
            context: 节点上下文
        """
        try:
            from src.services.nodes.distributed_log_service import distributed_log_service
            
            # 按 execution_id 分组处理
            for log in logs:
                execution_id = log.get("execution_id")
                log_type = log.get("log_type", "stdout")
                content = log.get("content", "")
                
                if not execution_id or not content:
                    continue
                
                # 使用分布式日志服务追加日志
                # 该服务会自动处理持久化和 WebSocket 广播
                await distributed_log_service.append_log(
                    execution_id=execution_id,
                    log_type=log_type,
                    content=content,
                    machine_code=context.metadata.get("machine_code"),
                )
            
            logger.debug(f"日志持久化完成 - 节点: {context.node_id}, 条数: {len(logs)}")
            
        except ImportError:
            logger.warning("distributed_log_service 不可用，跳过日志持久化")
        except Exception as e:
            logger.error(f"日志持久化失败: {e}")


# 全局处理器实例
log_handler = LogHandler()
