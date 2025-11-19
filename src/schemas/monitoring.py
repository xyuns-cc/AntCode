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

