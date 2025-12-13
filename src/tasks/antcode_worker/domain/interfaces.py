"""
领域接口 - Worker 节点抽象接口定义

本模块定义了传输层和服务层的抽象接口。
遵循依赖倒置原则，高层模块依赖抽象而非具体实现。

Requirements: 11.4
"""

from abc import ABC, abstractmethod
from typing import List, Callable, Awaitable, Optional

from .models import (
    ConnectionConfig,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskCancel,
    GrpcMetrics,
)


class TransportProtocol(ABC):
    """
    传输协议抽象接口
    
    定义了 Worker 与 Master 通信的标准接口。
    具体实现可以是 gRPC、HTTP 或 WebSocket。
    
    Requirements: 11.2
    """

    @abstractmethod
    async def connect(self, config: ConnectionConfig) -> bool:
        """
        建立连接
        
        Args:
            config: 连接配置
            
        Returns:
            是否连接成功
        """
        pass

    @abstractmethod
    async def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    async def send_heartbeat(self, heartbeat: Heartbeat) -> bool:
        """
        发送心跳
        
        Args:
            heartbeat: 心跳消息
            
        Returns:
            是否发送成功
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
        """
        pass

    @abstractmethod
    async def send_task_status(self, status: TaskStatus) -> bool:
        """
        发送任务状态更新
        
        Args:
            status: 任务状态
            
        Returns:
            是否发送成功
        """
        pass

    @abstractmethod
    def on_task_dispatch(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """
        注册任务分发回调
        
        Args:
            callback: 收到任务分发时的回调函数
        """
        pass

    @abstractmethod
    def on_task_cancel(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """
        注册任务取消回调
        
        Args:
            callback: 收到任务取消时的回调函数
        """
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """连接状态"""
        pass

    @property
    @abstractmethod
    def metrics(self) -> GrpcMetrics:
        """通信指标"""
        pass


class HeartbeatService(ABC):
    """
    心跳服务接口
    
    负责定期发送心跳消息，维护与 Master 的连接状态。
    
    Requirements: 11.3
    """

    @abstractmethod
    async def start(self, interval: int = 30):
        """
        启动心跳服务
        
        Args:
            interval: 心跳间隔（秒）
        """
        pass

    @abstractmethod
    async def stop(self):
        """停止心跳服务"""
        pass

    @abstractmethod
    async def send_heartbeat(self) -> bool:
        """
        立即发送一次心跳
        
        Returns:
            是否发送成功
        """
        pass

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """心跳服务是否运行中"""
        pass

    @property
    @abstractmethod
    def last_heartbeat_time(self) -> Optional[float]:
        """上次心跳时间戳"""
        pass


class LogService(ABC):
    """
    日志服务接口
    
    负责缓冲和批量发送日志到 Master。
    
    Requirements: 11.3
    """

    @abstractmethod
    async def add(self, execution_id: str, log_type: str, content: str):
        """
        添加日志行
        
        Args:
            execution_id: 执行 ID
            log_type: 日志类型 ("stdout" | "stderr")
            content: 日志内容
        """
        pass

    @abstractmethod
    async def flush(self, execution_id: Optional[str] = None):
        """
        刷新日志缓冲区
        
        Args:
            execution_id: 指定执行 ID，None 表示刷新所有
        """
        pass

    @abstractmethod
    async def start(self):
        """启动后台刷新任务"""
        pass

    @abstractmethod
    async def stop(self):
        """停止并刷新剩余日志"""
        pass

    @property
    @abstractmethod
    def buffer_size(self) -> int:
        """当前缓冲区大小"""
        pass


class TaskService(ABC):
    """
    任务服务接口
    
    负责处理任务分发、状态上报和取消。
    
    Requirements: 11.3
    """

    @abstractmethod
    async def report_status(
        self,
        execution_id: str,
        status: str,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        上报任务状态
        
        Args:
            execution_id: 执行 ID
            status: 状态
            exit_code: 退出码（可选）
            error_message: 错误消息（可选）
            
        Returns:
            是否上报成功
        """
        pass

    @abstractmethod
    def on_task_received(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """
        注册任务接收回调
        
        Args:
            callback: 收到任务时的回调函数
        """
        pass

    @abstractmethod
    def on_task_cancelled(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """
        注册任务取消回调
        
        Args:
            callback: 收到取消请求时的回调函数
        """
        pass


class MetricsService(ABC):
    """
    指标服务接口
    
    负责收集和提供系统指标。
    
    Requirements: 11.3
    """

    @abstractmethod
    def get_system_metrics(self) -> dict:
        """
        获取系统指标
        
        Returns:
            包含 CPU、内存、磁盘等指标的字典
        """
        pass

    @abstractmethod
    def get_os_info(self) -> dict:
        """
        获取操作系统信息
        
        Returns:
            包含 OS 类型、版本等信息的字典
        """
        pass

    @abstractmethod
    def get_communication_metrics(self) -> dict:
        """
        获取通信指标
        
        Returns:
            包含消息计数、延迟等指标的字典
        """
        pass
