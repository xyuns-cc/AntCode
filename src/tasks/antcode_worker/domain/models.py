"""
领域模型 - Worker 节点核心数据结构

本模块定义了与传输协议无关的数据模型。
这些模型用于在各层之间传递数据，不依赖任何外部库。

Requirements: 11.4
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List


class ConnectionState(str, Enum):
    """连接状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"  # gRPC 失败，使用 HTTP


class Protocol(str, Enum):
    """通信协议"""
    NONE = "none"
    HTTP = "http"
    GRPC = "grpc"
    WEBSOCKET = "websocket"  # 保留兼容性


@dataclass
class ConnectionConfig:
    """连接配置"""
    master_url: str
    node_id: str
    api_key: str
    machine_code: str
    secret_key: Optional[str] = None
    grpc_port: int = 50051
    prefer_grpc: bool = True
    heartbeat_interval: int = 30
    reconnect_base_delay: float = 5.0
    reconnect_max_delay: float = 60.0


@dataclass
class OSInfo:
    """操作系统信息"""
    os_type: str
    os_version: str
    python_version: str
    machine_arch: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "os_type": self.os_type,
            "os_version": self.os_version,
            "python_version": self.python_version,
            "machine_arch": self.machine_arch,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "OSInfo":
        return cls(
            os_type=data.get("os_type", ""),
            os_version=data.get("os_version", ""),
            python_version=data.get("python_version", ""),
            machine_arch=data.get("machine_arch", ""),
        )


@dataclass
class Metrics:
    """系统指标"""
    cpu: float = 0.0
    memory: float = 0.0
    disk: float = 0.0
    running_tasks: int = 0
    max_concurrent_tasks: int = 5
    task_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu": self.cpu,
            "memory": self.memory,
            "disk": self.disk,
            "running_tasks": self.running_tasks,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "task_count": self.task_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Metrics":
        return cls(
            cpu=data.get("cpu", 0.0),
            memory=data.get("memory", 0.0),
            disk=data.get("disk", 0.0),
            running_tasks=data.get("running_tasks", 0),
            max_concurrent_tasks=data.get("max_concurrent_tasks", 5),
            task_count=data.get("task_count", 0),
        )


@dataclass
class Heartbeat:
    """心跳消息"""
    node_id: str
    status: str
    metrics: Metrics
    os_info: OSInfo
    timestamp: datetime = field(default_factory=datetime.now)
    capabilities: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "metrics": self.metrics.to_dict(),
            "os_info": self.os_info.to_dict(),
            "timestamp": self.timestamp.isoformat(),
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Heartbeat":
        return cls(
            node_id=data.get("node_id", ""),
            status=data.get("status", ""),
            metrics=Metrics.from_dict(data.get("metrics", {})),
            os_info=OSInfo.from_dict(data.get("os_info", {})),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(),
            capabilities=data.get("capabilities", {}),
        )


@dataclass
class LogEntry:
    """日志条目"""
    execution_id: str
    log_type: str  # "stdout" | "stderr"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "log_type": self.log_type,
            "content": self.content,
            "timestamp": self.timestamp.timestamp() if isinstance(self.timestamp, datetime) else self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LogEntry":
        ts = data.get("timestamp")
        if isinstance(ts, (int, float)):
            timestamp = datetime.fromtimestamp(ts)
        elif isinstance(ts, str):
            timestamp = datetime.fromisoformat(ts)
        else:
            timestamp = datetime.now()
        return cls(
            execution_id=data.get("execution_id", ""),
            log_type=data.get("log_type", "stdout"),
            content=data.get("content", ""),
            timestamp=timestamp,
        )


@dataclass
class TaskStatus:
    """任务状态更新"""
    execution_id: str
    status: str
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "execution_id": self.execution_id,
            "status": self.status,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.error_message is not None:
            result["error_message"] = self.error_message
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStatus":
        return cls(
            execution_id=data.get("execution_id", ""),
            status=data.get("status", ""),
            exit_code=data.get("exit_code"),
            error_message=data.get("error_message"),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(),
        )


@dataclass
class TaskDispatch:
    """任务分发消息"""
    task_id: str
    project_id: str
    project_type: str = "code"
    priority: int = 2
    params: Dict[str, str] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)
    timeout: int = 3600
    download_url: Optional[str] = None
    file_hash: Optional[str] = None
    entry_point: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_type": self.project_type,
            "priority": self.priority,
            "params": self.params,
            "environment": self.environment,
            "timeout": self.timeout,
            "download_url": self.download_url,
            "file_hash": self.file_hash,
            "entry_point": self.entry_point,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskDispatch":
        return cls(
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            project_type=data.get("project_type", "code"),
            priority=data.get("priority", 2),
            params=data.get("params", {}),
            environment=data.get("environment", {}),
            timeout=data.get("timeout", 3600),
            download_url=data.get("download_url"),
            file_hash=data.get("file_hash"),
            entry_point=data.get("entry_point"),
        )


@dataclass
class TaskAck:
    """任务确认消息"""
    task_id: str
    accepted: bool
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "task_id": self.task_id,
            "accepted": self.accepted,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskAck":
        return cls(
            task_id=data.get("task_id", ""),
            accepted=data.get("accepted", False),
            reason=data.get("reason"),
        )


@dataclass
class TaskCancel:
    """任务取消消息"""
    task_id: str
    execution_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskCancel":
        return cls(
            task_id=data.get("task_id", ""),
            execution_id=data.get("execution_id", ""),
        )


@dataclass
class CancelAck:
    """取消确认消息"""
    task_id: str
    success: bool
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "task_id": self.task_id,
            "success": self.success,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CancelAck":
        return cls(
            task_id=data.get("task_id", ""),
            success=data.get("success", False),
            reason=data.get("reason"),
        )


@dataclass
class GrpcMetrics:
    """
    gRPC 通信指标
    
    跟踪消息发送/接收计数、连接持续时间、重连次数和延迟统计。
    
    Requirements: 10.1, 10.2, 10.3
    """
    # 消息计数
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    
    # 连接统计
    reconnect_count: int = 0
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    total_connection_time_seconds: float = 0.0
    
    # 延迟统计
    latency_samples: List[float] = field(default_factory=list)
    _max_latency_samples: int = field(default=100, repr=False)
    
    # 错误统计
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None

    @property
    def avg_latency_ms(self) -> float:
        """平均延迟（毫秒）"""
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)

    @property
    def min_latency_ms(self) -> float:
        """最小延迟（毫秒）"""
        if not self.latency_samples:
            return 0.0
        return min(self.latency_samples)

    @property
    def max_latency_ms(self) -> float:
        """最大延迟（毫秒）"""
        if not self.latency_samples:
            return 0.0
        return max(self.latency_samples)

    @property
    def p95_latency_ms(self) -> float:
        """P95 延迟（毫秒）"""
        if not self.latency_samples:
            return 0.0
        sorted_samples = sorted(self.latency_samples)
        index = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(index, len(sorted_samples) - 1)]

    @property
    def p99_latency_ms(self) -> float:
        """P99 延迟（毫秒）"""
        if not self.latency_samples:
            return 0.0
        sorted_samples = sorted(self.latency_samples)
        index = int(len(sorted_samples) * 0.99)
        return sorted_samples[min(index, len(sorted_samples) - 1)]

    @property
    def connection_duration_seconds(self) -> float:
        """当前连接持续时间（秒）"""
        if self.connected_at is None:
            return 0.0
        end_time = self.disconnected_at or datetime.now()
        return (end_time - self.connected_at).total_seconds()

    @property
    def latency_sample_count(self) -> int:
        """延迟样本数量"""
        return len(self.latency_samples)

    def record_latency(self, latency_ms: float):
        """
        记录延迟样本
        
        Requirements: 10.3
        """
        self.latency_samples.append(latency_ms)
        # 保留最近 N 个样本
        if len(self.latency_samples) > self._max_latency_samples:
            self.latency_samples = self.latency_samples[-self._max_latency_samples:]

    def record_message_sent(self, byte_size: int = 0):
        """
        记录发送的消息
        
        Requirements: 10.1
        """
        self.messages_sent += 1
        self.bytes_sent += byte_size

    def record_message_received(self, byte_size: int = 0):
        """
        记录接收的消息
        
        Requirements: 10.1
        """
        self.messages_received += 1
        self.bytes_received += byte_size

    def record_connection(self):
        """
        记录连接建立
        
        Requirements: 10.2
        """
        now = datetime.now()
        # 如果之前有连接，累加连接时间
        if self.connected_at and self.disconnected_at:
            self.total_connection_time_seconds += (
                self.disconnected_at - self.connected_at
            ).total_seconds()
        self.connected_at = now
        self.disconnected_at = None

    def record_disconnection(self):
        """
        记录连接断开
        
        Requirements: 10.2
        """
        self.disconnected_at = datetime.now()
        if self.connected_at:
            self.total_connection_time_seconds += (
                self.disconnected_at - self.connected_at
            ).total_seconds()

    def record_reconnection(self):
        """
        记录重连
        
        Requirements: 10.2
        """
        self.reconnect_count += 1
        self.record_connection()

    def record_error(
        self,
        error_message: str,
        error_code: Optional[str] = None,
        operation: Optional[str] = None,
    ):
        """
        记录错误，包含上下文信息
        
        Args:
            error_message: 错误消息
            error_code: gRPC 错误码
            operation: 发生错误的操作
        
        Requirements: 10.5
        """
        from loguru import logger
        
        self.error_count += 1
        self.last_error = error_message
        self.last_error_time = datetime.now()
        
        # 构建带上下文的日志
        context = {
            "timestamp": self.last_error_time.isoformat(),
            "error_type": "grpc_client_error",
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "reconnect_count": self.reconnect_count,
        }
        
        if error_code:
            context["error_code"] = error_code
        if operation:
            context["operation"] = operation
        
        log_parts = [f"gRPC 客户端错误: {error_message}"]
        if error_code:
            log_parts.append(f"错误码: {error_code}")
        if operation:
            log_parts.append(f"操作: {operation}")
        
        log_message = " | ".join(log_parts)
        logger.bind(**context).error(log_message)

    def reset(self):
        """重置所有指标"""
        self.messages_sent = 0
        self.messages_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.reconnect_count = 0
        self.connected_at = None
        self.disconnected_at = None
        self.total_connection_time_seconds = 0.0
        self.latency_samples = []
        self.error_count = 0
        self.last_error = None
        self.last_error_time = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "reconnect_count": self.reconnect_count,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "disconnected_at": self.disconnected_at.isoformat() if self.disconnected_at else None,
            "connection_duration_seconds": round(self.connection_duration_seconds, 2),
            "total_connection_time_seconds": round(self.total_connection_time_seconds, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "min_latency_ms": round(self.min_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "latency_sample_count": self.latency_sample_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None,
        }
