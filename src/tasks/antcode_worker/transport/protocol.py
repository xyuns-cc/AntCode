"""
传输协议抽象基类

定义了 Worker 与 Master 通信的标准接口。
所有具体传输实现（gRPC、HTTP）都必须实现此接口。

Requirements: 11.2
"""

from abc import ABC, abstractmethod
from typing import List, Callable, Awaitable, Optional, Dict, Any

from ..domain.models import (
    ConnectionConfig,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskCancel,
    GrpcMetrics,
)


class TransportError(Exception):
    """传输层错误基类"""
    pass


class ConnectionError(TransportError):
    """连接错误"""
    pass


class SendError(TransportError):
    """发送错误"""
    pass


class TransportProtocol(ABC):
    """
    传输协议抽象接口
    
    定义了 Worker 与 Master 通信的标准接口。
    具体实现可以是 gRPC 或 HTTP。
    
    设计原则：
    1. 协议无关 - 业务逻辑不依赖具体传输协议
    2. 异步优先 - 所有 I/O 操作都是异步的
    3. 回调驱动 - 使用回调处理来自 Master 的消息
    
    Requirements: 11.2
    """

    @abstractmethod
    async def connect(self, config: ConnectionConfig) -> bool:
        """
        建立连接
        
        Args:
            config: 连接配置，包含 master_url、node_id、api_key、access_token 等
            
        Returns:
            是否连接成功
            
        Raises:
            ConnectionError: 连接失败时抛出
        """
        pass

    @abstractmethod
    async def disconnect(self):
        """
        断开连接
        
        应该优雅地关闭连接，确保：
        1. 刷新所有待发送的消息
        2. 关闭底层连接
        3. 清理资源
        """
        pass

    @abstractmethod
    async def send_heartbeat(self, heartbeat: Heartbeat) -> bool:
        """
        发送心跳消息
        
        Args:
            heartbeat: 心跳消息，包含节点状态和指标
            
        Returns:
            是否发送成功
            
        Raises:
            SendError: 发送失败时抛出
        """
        pass

    @abstractmethod
    async def send_logs(self, logs: List[LogEntry]) -> bool:
        """
        发送日志批次
        
        Args:
            logs: 日志条目列表
            
        Returns:
            是否发送成功
            
        Note:
            实现应该支持压缩大批量日志
        """
        pass

    @abstractmethod
    async def send_task_status(self, status: TaskStatus) -> bool:
        """
        发送任务状态更新
        
        Args:
            status: 任务状态，包含 execution_id、status、exit_code 等
            
        Returns:
            是否发送成功
            
        Note:
            状态更新应该立即发送，不经过缓冲
        """
        pass

    @abstractmethod
    def on_task_dispatch(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """
        注册任务分发回调
        
        当收到 Master 发送的任务分发消息时，调用此回调。
        
        Args:
            callback: 异步回调函数，参数为 TaskDispatch 对象
        """
        pass

    @abstractmethod
    def on_task_cancel(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """
        注册任务取消回调
        
        当收到 Master 发送的任务取消消息时，调用此回调。
        
        Args:
            callback: 异步回调函数，参数为 TaskCancel 对象
        """
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """
        连接状态
        
        Returns:
            True 如果当前已连接，False 否则
        """
        pass

    @property
    @abstractmethod
    def metrics(self) -> GrpcMetrics:
        """
        通信指标
        
        Returns:
            GrpcMetrics 对象，包含消息计数、延迟等指标
        """
        pass

    @property
    def protocol_name(self) -> str:
        """
        协议名称
        
        Returns:
            协议名称字符串，如 "grpc"、"http"、"websocket"
        """
        return "unknown"

    async def send_task_ack(self, task_id: str, accepted: bool, reason: Optional[str] = None) -> bool:
        """
        发送任务确认
        
        Args:
            task_id: 任务 ID
            accepted: 是否接受任务
            reason: 拒绝原因（如果拒绝）
            
        Returns:
            是否发送成功
            
        Note:
            默认实现返回 True，子类可以覆盖
        """
        return True

    async def send_cancel_ack(self, task_id: str, success: bool, reason: Optional[str] = None) -> bool:
        """
        发送取消确认
        
        Args:
            task_id: 任务 ID
            success: 是否成功取消
            reason: 失败原因（如果失败）
            
        Returns:
            是否发送成功
            
        Note:
            默认实现返回 True，子类可以覆盖
        """
        return True

    def get_stats(self) -> Dict[str, Any]:
        """
        获取传输层统计信息
        
        Returns:
            包含连接状态、消息计数等信息的字典
        """
        return {
            "protocol": self.protocol_name,
            "connected": self.is_connected,
            "metrics": self.metrics.to_dict() if self.metrics else {},
        }
