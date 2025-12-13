"""
领域层 - Worker 节点核心数据模型和接口

本模块定义了与传输协议无关的领域模型、接口和事件。
遵循分层架构原则，领域层不依赖任何外部库或传输协议。

Requirements: 11.4
"""

from .models import (
    ConnectionState,
    Protocol,
    ConnectionConfig,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskAck,
    TaskCancel,
    CancelAck,
    GrpcMetrics,
    OSInfo,
    Metrics,
)
from .interfaces import (
    TransportProtocol,
    HeartbeatService,
    LogService,
    TaskService,
    MetricsService,
)
from .events import (
    DomainEvent,
    ConnectionStateChanged,
    ProtocolChanged,
    HeartbeatSent,
    HeartbeatFailed,
    LogBatchSent,
    TaskReceived,
    TaskStatusChanged,
    TaskCancelled,
    MessageDropped,
)

__all__ = [
    # Models
    "ConnectionState",
    "Protocol",
    "ConnectionConfig",
    "Heartbeat",
    "LogEntry",
    "TaskStatus",
    "TaskDispatch",
    "TaskAck",
    "TaskCancel",
    "CancelAck",
    "GrpcMetrics",
    "OSInfo",
    "Metrics",
    # Interfaces
    "TransportProtocol",
    "HeartbeatService",
    "LogService",
    "TaskService",
    "MetricsService",
    # Events
    "DomainEvent",
    "ConnectionStateChanged",
    "ProtocolChanged",
    "HeartbeatSent",
    "HeartbeatFailed",
    "LogBatchSent",
    "TaskReceived",
    "TaskStatusChanged",
    "TaskCancelled",
    "MessageDropped",
]
