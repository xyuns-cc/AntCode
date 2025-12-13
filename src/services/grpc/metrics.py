"""
gRPC 指标服务

收集和提供 gRPC 服务器端的指标数据。

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

from loguru import logger


def log_grpc_error(
    error_message: str,
    error_code: Optional[str] = None,
    node_id: Optional[str] = None,
    operation: Optional[str] = None,
    active_connections: Optional[int] = None,
    total_messages_sent: Optional[int] = None,
    total_messages_received: Optional[int] = None,
    extra_context: Optional[Dict[str, Any]] = None,
):
    """
    记录 gRPC 错误，包含丰富的上下文信息
    
    提供结构化的错误日志，便于调试和问题排查。
    
    Args:
        error_message: 错误消息
        error_code: gRPC 错误码（如 UNAVAILABLE, DEADLINE_EXCEEDED）
        node_id: 相关节点 ID
        operation: 发生错误的操作（如 send_heartbeat, receive_message）
        active_connections: 当前活跃连接数
        total_messages_sent: 总发送消息数
        total_messages_received: 总接收消息数
        extra_context: 额外的上下文信息
    
    Requirements: 10.5
    """
    # 构建上下文字典
    context = {
        "timestamp": datetime.now().isoformat(),
        "error_type": "grpc_error",
    }
    
    if error_code:
        context["error_code"] = error_code
    if node_id:
        context["node_id"] = node_id
    if operation:
        context["operation"] = operation
    if active_connections is not None:
        context["active_connections"] = active_connections
    if total_messages_sent is not None:
        context["messages_sent"] = total_messages_sent
    if total_messages_received is not None:
        context["messages_received"] = total_messages_received
    if extra_context:
        context.update(extra_context)
    
    # 构建日志消息
    log_parts = [f"gRPC 错误: {error_message}"]
    
    if error_code:
        log_parts.append(f"错误码: {error_code}")
    if node_id:
        log_parts.append(f"节点: {node_id}")
    if operation:
        log_parts.append(f"操作: {operation}")
    
    log_message = " | ".join(log_parts)
    
    # 使用 loguru 的结构化日志
    logger.bind(**context).error(log_message)


def log_grpc_warning(
    message: str,
    node_id: Optional[str] = None,
    operation: Optional[str] = None,
    extra_context: Optional[Dict[str, Any]] = None,
):
    """
    记录 gRPC 警告，包含上下文信息
    
    Args:
        message: 警告消息
        node_id: 相关节点 ID
        operation: 发生警告的操作
        extra_context: 额外的上下文信息
    
    Requirements: 10.5
    """
    context = {
        "timestamp": datetime.now().isoformat(),
        "warning_type": "grpc_warning",
    }
    
    if node_id:
        context["node_id"] = node_id
    if operation:
        context["operation"] = operation
    if extra_context:
        context.update(extra_context)
    
    log_parts = [f"gRPC 警告: {message}"]
    if node_id:
        log_parts.append(f"节点: {node_id}")
    if operation:
        log_parts.append(f"操作: {operation}")
    
    log_message = " | ".join(log_parts)
    logger.bind(**context).warning(log_message)


def log_grpc_connection_event(
    event_type: str,
    node_id: str,
    success: bool = True,
    error_message: Optional[str] = None,
    extra_context: Optional[Dict[str, Any]] = None,
):
    """
    记录 gRPC 连接事件
    
    Args:
        event_type: 事件类型（connect, disconnect, reconnect）
        node_id: 节点 ID
        success: 是否成功
        error_message: 错误消息（如果失败）
        extra_context: 额外的上下文信息
    
    Requirements: 10.5
    """
    context = {
        "timestamp": datetime.now().isoformat(),
        "event_type": f"grpc_{event_type}",
        "node_id": node_id,
        "success": success,
    }
    
    if extra_context:
        context.update(extra_context)
    
    if success:
        log_message = f"gRPC 连接事件: {event_type} | 节点: {node_id} | 成功"
        logger.bind(**context).info(log_message)
    else:
        context["error"] = error_message
        log_message = f"gRPC 连接事件: {event_type} | 节点: {node_id} | 失败: {error_message}"
        logger.bind(**context).error(log_message)


@dataclass
class GrpcServerMetricsCollector:
    """
    gRPC 服务器指标收集器
    
    跟踪服务器端的消息计数、连接统计和延迟数据。
    
    Requirements: 10.1, 10.2, 10.3
    """
    # 消息计数
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    
    # 连接统计
    total_connections: int = 0
    active_connections: int = 0
    reconnect_count: int = 0
    
    # 延迟统计
    latency_samples: List[float] = field(default_factory=list)
    _max_latency_samples: int = field(default=100, repr=False)
    
    # 错误统计
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None
    
    # 服务器状态
    started_at: Optional[datetime] = None
    
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
    def latency_sample_count(self) -> int:
        """延迟样本数量"""
        return len(self.latency_samples)
    
    @property
    def uptime_seconds(self) -> float:
        """服务器运行时间（秒）"""
        if self.started_at is None:
            return 0.0
        return (datetime.now() - self.started_at).total_seconds()
    
    def record_message_sent(self, byte_size: int = 0):
        """记录发送的消息"""
        self.messages_sent += 1
        self.bytes_sent += byte_size
    
    def record_message_received(self, byte_size: int = 0):
        """记录接收的消息"""
        self.messages_received += 1
        self.bytes_received += byte_size
    
    def record_latency(self, latency_ms: float):
        """记录延迟样本"""
        self.latency_samples.append(latency_ms)
        if len(self.latency_samples) > self._max_latency_samples:
            self.latency_samples = self.latency_samples[-self._max_latency_samples:]
    
    def record_connection(self):
        """记录新连接"""
        self.total_connections += 1
        self.active_connections += 1
    
    def record_disconnection(self):
        """记录断开连接"""
        if self.active_connections > 0:
            self.active_connections -= 1
    
    def record_error(
        self,
        error_message: str,
        error_code: Optional[str] = None,
        node_id: Optional[str] = None,
        operation: Optional[str] = None,
    ):
        """
        记录错误，包含丰富的上下文信息
        
        Args:
            error_message: 错误消息
            error_code: gRPC 错误码（如 UNAVAILABLE, DEADLINE_EXCEEDED）
            node_id: 相关节点 ID
            operation: 发生错误的操作（如 send_heartbeat, receive_message）
        
        Requirements: 10.5
        """
        self.error_count += 1
        self.last_error = error_message
        self.last_error_time = datetime.now()
        
        # 构建带上下文的日志消息
        log_grpc_error(
            error_message=error_message,
            error_code=error_code,
            node_id=node_id,
            operation=operation,
            active_connections=self.active_connections,
            total_messages_sent=self.messages_sent,
            total_messages_received=self.messages_received,
        )
    
    def record_server_start(self):
        """记录服务器启动"""
        self.started_at = datetime.now()
    
    def reset(self):
        """重置所有指标"""
        self.messages_sent = 0
        self.messages_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.total_connections = 0
        self.active_connections = 0
        self.reconnect_count = 0
        self.latency_samples = []
        self.error_count = 0
        self.last_error = None
        self.last_error_time = None
        self.started_at = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "total_connections": self.total_connections,
            "active_connections": self.active_connections,
            "reconnect_count": self.reconnect_count,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "min_latency_ms": round(self.min_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "latency_sample_count": self.latency_sample_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "uptime_seconds": round(self.uptime_seconds, 2),
        }


# 全局指标收集器实例
grpc_metrics_collector = GrpcServerMetricsCollector()


def get_grpc_metrics_collector() -> GrpcServerMetricsCollector:
    """获取全局 gRPC 指标收集器"""
    return grpc_metrics_collector
