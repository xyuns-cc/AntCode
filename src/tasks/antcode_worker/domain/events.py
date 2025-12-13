"""
领域事件 - Worker 节点事件定义

本模块定义了领域事件，用于解耦各层之间的通信。
事件驱动架构允许组件之间松耦合地响应状态变化。

Requirements: 11.4
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, Dict

from .models import ConnectionState, Protocol, TaskDispatch, TaskCancel


@dataclass
class DomainEvent:
    """领域事件基类"""
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: "")

    def __post_init__(self):
        if not self.event_id:
            import uuid
            self.event_id = str(uuid.uuid4())


@dataclass
class ConnectionStateChanged(DomainEvent):
    """连接状态变更事件"""
    old_state: ConnectionState = ConnectionState.DISCONNECTED
    new_state: ConnectionState = ConnectionState.DISCONNECTED
    reason: Optional[str] = None


@dataclass
class ProtocolChanged(DomainEvent):
    """协议切换事件"""
    old_protocol: Protocol = Protocol.NONE
    new_protocol: Protocol = Protocol.NONE
    reason: Optional[str] = None


@dataclass
class HeartbeatSent(DomainEvent):
    """心跳发送成功事件"""
    node_id: str = ""
    latency_ms: float = 0.0


@dataclass
class HeartbeatFailed(DomainEvent):
    """心跳发送失败事件"""
    node_id: str = ""
    error: str = ""
    consecutive_failures: int = 0


@dataclass
class LogBatchSent(DomainEvent):
    """日志批次发送事件"""
    execution_id: str = ""
    log_count: int = 0
    compressed: bool = False
    bytes_sent: int = 0


@dataclass
class TaskReceived(DomainEvent):
    """任务接收事件"""
    task: Optional[TaskDispatch] = None
    accepted: bool = True
    reject_reason: Optional[str] = None


@dataclass
class TaskStatusChanged(DomainEvent):
    """任务状态变更事件"""
    execution_id: str = ""
    old_status: str = ""
    new_status: str = ""
    exit_code: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class TaskCancelled(DomainEvent):
    """任务取消事件"""
    cancel_request: Optional[TaskCancel] = None
    success: bool = False
    reason: Optional[str] = None


@dataclass
class MessageDropped(DomainEvent):
    """消息丢弃事件（背压）"""
    message_type: str = ""
    reason: str = ""
    dropped_count: int = 1


@dataclass
class ReconnectionAttempt(DomainEvent):
    """重连尝试事件"""
    attempt_number: int = 0
    delay_seconds: float = 0.0
    protocol: Protocol = Protocol.NONE


@dataclass
class ReconnectionSuccess(DomainEvent):
    """重连成功事件"""
    attempt_number: int = 0
    protocol: Protocol = Protocol.NONE
    pending_messages_count: int = 0


@dataclass
class ReconnectionFailed(DomainEvent):
    """重连失败事件"""
    attempt_number: int = 0
    error: str = ""
    next_delay_seconds: float = 0.0


@dataclass
class ProtocolFallback(DomainEvent):
    """协议降级事件"""
    from_protocol: Protocol = Protocol.NONE
    to_protocol: Protocol = Protocol.NONE
    reason: str = ""


@dataclass
class ProtocolUpgrade(DomainEvent):
    """协议升级事件"""
    from_protocol: Protocol = Protocol.NONE
    to_protocol: Protocol = Protocol.NONE


class EventBus:
    """
    简单的事件总线实现
    
    用于在组件之间发布和订阅领域事件。
    """

    def __init__(self):
        self._handlers: Dict[type, list] = {}

    def subscribe(self, event_type: type, handler):
        """
        订阅事件
        
        Args:
            event_type: 事件类型
            handler: 事件处理函数（同步或异步）
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler):
        """
        取消订阅
        
        Args:
            event_type: 事件类型
            handler: 事件处理函数
        """
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    async def publish(self, event: DomainEvent):
        """
        发布事件
        
        Args:
            event: 领域事件
        """
        import asyncio
        import inspect

        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                # 记录错误但不中断其他处理器
                import logging
                logging.error(f"Event handler error for {event_type.__name__}: {e}")

    def publish_sync(self, event: DomainEvent):
        """
        同步发布事件（仅调用同步处理器）
        
        Args:
            event: 领域事件
        """
        import inspect

        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        for handler in handlers:
            try:
                if not inspect.iscoroutinefunction(handler):
                    handler(event)
            except Exception as e:
                import logging
                logging.error(f"Event handler error for {event_type.__name__}: {e}")


# 全局事件总线实例
event_bus = EventBus()
