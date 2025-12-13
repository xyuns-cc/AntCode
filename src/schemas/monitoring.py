from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class NodeStatus(BaseModel):
    cpu_percent: Optional[float] = Field(None, description="CPU使用率(%)")
    memory_percent: Optional[float] = Field(None, description="内存使用率(%)")
    memory_used_mb: Optional[int] = Field(None, description="内存使用量(MB)")
    disk_percent: Optional[float] = Field(None, description="磁盘使用率(%)")
    network_sent_mb: Optional[float] = Field(None, description="网络发送(MB)")
    network_recv_mb: Optional[float] = Field(None, description="网络接收(MB)")
    uptime_seconds: Optional[int] = Field(None, description="节点运行时间(秒)")
    status: Optional[str] = Field(None, description="节点状态")
    update_time: Optional[int] = Field(None, description="指标更新时间戳")


class NodeSpiderStats(BaseModel):
    items_scraped: Optional[int] = 0
    requests_total: Optional[int] = 0
    requests_failed: Optional[int] = 0
    pages_crawled: Optional[int] = 0
    avg_response_time_ms: Optional[int] = 0
    scheduler_enqueued: Optional[int] = 0
    scheduler_dequeued: Optional[int] = 0


class NodeSummary(BaseModel):
    node_id: str
    status: NodeStatus
    spider: NodeSpiderStats


class NodeRealtimePoint(BaseModel):
    timestamp: float
    tasks_total: Optional[int] = None
    tasks_success: Optional[int] = None
    tasks_failed: Optional[int] = None
    tasks_running: Optional[int] = None
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    memory_used_mb: Optional[int] = None
    disk_percent: Optional[float] = None
    network_sent_mb: Optional[float] = None
    network_recv_mb: Optional[float] = None
    pages_crawled: Optional[int] = None
    items_scraped: Optional[int] = None
    requests_total: Optional[int] = None
    requests_failed: Optional[int] = None
    avg_response_time_ms: Optional[int] = None
    error_timeout: Optional[int] = None
    error_network: Optional[int] = None
    error_parse: Optional[int] = None
    error_other: Optional[int] = None


class NodeRealtimeResponse(BaseModel):
    node_id: str
    data: List[NodeRealtimePoint]


class ClusterSummaryResponse(BaseModel):
    nodes_online: int
    requests_total: int
    requests_failed: int
    items_scraped: int
    pages_crawled: int
    success_rate: float


class NodeHistoryItem(BaseModel):
    timestamp: datetime
    tasks_total: Optional[int] = None
    tasks_success: Optional[int] = None
    tasks_failed: Optional[int] = None
    tasks_running: Optional[int] = None
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    memory_used_mb: Optional[int] = None
    disk_percent: Optional[float] = None
    network_sent_mb: Optional[float] = None
    network_recv_mb: Optional[float] = None
    pages_crawled: Optional[int] = None
    items_scraped: Optional[int] = None
    requests_total: Optional[int] = None
    requests_failed: Optional[int] = None
    avg_response_time_ms: Optional[int] = None
    error_timeout: Optional[int] = None
    error_network: Optional[int] = None
    error_parse: Optional[int] = None
    error_other: Optional[int] = None


class HistoryQueryResponse(BaseModel):
    node_id: str
    metric_type: str
    data: List[NodeHistoryItem]
    count: int



# gRPC 指标相关 Schema
class GrpcConnectionStats(BaseModel):
    """gRPC 连接统计"""
    connected_nodes: int = Field(0, description="当前连接的节点数")
    total_connections: int = Field(0, description="总连接数（历史）")
    active_streams: int = Field(0, description="活跃的双向流数量")


class GrpcMessageStats(BaseModel):
    """gRPC 消息统计"""
    messages_sent: int = Field(0, description="发送的消息数")
    messages_received: int = Field(0, description="接收的消息数")
    bytes_sent: int = Field(0, description="发送的字节数")
    bytes_received: int = Field(0, description="接收的字节数")


class GrpcLatencyStats(BaseModel):
    """gRPC 延迟统计"""
    avg_latency_ms: float = Field(0.0, description="平均延迟（毫秒）")
    min_latency_ms: float = Field(0.0, description="最小延迟（毫秒）")
    max_latency_ms: float = Field(0.0, description="最大延迟（毫秒）")
    p95_latency_ms: float = Field(0.0, description="P95 延迟（毫秒）")
    p99_latency_ms: float = Field(0.0, description="P99 延迟（毫秒）")
    sample_count: int = Field(0, description="延迟样本数量")


class GrpcErrorStats(BaseModel):
    """gRPC 错误统计"""
    error_count: int = Field(0, description="错误总数")
    last_error: Optional[str] = Field(None, description="最近一次错误信息")
    last_error_time: Optional[datetime] = Field(None, description="最近一次错误时间")
    reconnect_count: int = Field(0, description="重连次数")


class GrpcServerMetrics(BaseModel):
    """gRPC 服务器指标"""
    enabled: bool = Field(True, description="gRPC 服务是否启用")
    running: bool = Field(False, description="gRPC 服务器是否运行中")
    port: int = Field(50051, description="gRPC 服务器端口")
    connection: GrpcConnectionStats = Field(default_factory=GrpcConnectionStats)
    messages: GrpcMessageStats = Field(default_factory=GrpcMessageStats)
    latency: GrpcLatencyStats = Field(default_factory=GrpcLatencyStats)
    errors: GrpcErrorStats = Field(default_factory=GrpcErrorStats)
    uptime_seconds: Optional[float] = Field(None, description="服务器运行时间（秒）")
    started_at: Optional[datetime] = Field(None, description="服务器启动时间")


class GrpcMetricsResponse(BaseModel):
    """gRPC 指标响应"""
    server: GrpcServerMetrics = Field(default_factory=GrpcServerMetrics)
    timestamp: datetime = Field(default_factory=datetime.now)
