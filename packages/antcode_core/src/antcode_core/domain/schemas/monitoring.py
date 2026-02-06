from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WorkerStatus(BaseModel):
    cpu_percent: float = Field(0.0, description="CPU使用率(%)")
    memory_percent: float = Field(0.0, description="内存使用率(%)")
    memory_used_mb: int = Field(0, description="内存使用量(MB)")
    disk_percent: float = Field(0.0, description="磁盘使用率(%)")
    network_sent_mb: float = Field(0.0, description="网络发送(MB)")
    network_recv_mb: float = Field(0.0, description="网络接收(MB)")
    uptime_seconds: int = Field(0, description="Worker 运行时间(秒)")
    status: str = Field("", description="Worker 状态")
    update_time: int = Field(0, description="指标更新时间戳")


class WorkerSpiderStats(BaseModel):
    items_scraped: int | None = 0
    requests_total: int | None = 0
    requests_failed: int | None = 0
    pages_crawled: int | None = 0
    avg_response_time_ms: int | None = 0
    scheduler_enqueued: int | None = 0
    scheduler_dequeued: int | None = 0


class WorkerSummary(BaseModel):
    worker_id: str
    status: WorkerStatus
    spider: WorkerSpiderStats


class WorkerRealtimePoint(BaseModel):
    timestamp: float
    tasks_total: int = 0
    tasks_success: int = 0
    tasks_failed: int = 0
    tasks_running: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: int = 0
    disk_percent: float = 0.0
    network_sent_mb: float = 0.0
    network_recv_mb: float = 0.0
    pages_crawled: int = 0
    items_scraped: int = 0
    requests_total: int = 0
    requests_failed: int = 0
    avg_response_time_ms: int = 0
    error_timeout: int = 0
    error_network: int = 0
    error_parse: int = 0
    error_other: int = 0


class WorkerRealtimeResponse(BaseModel):
    worker_id: str
    data: list[WorkerRealtimePoint]


class ClusterSummaryResponse(BaseModel):
    workers_online: int
    requests_total: int
    requests_failed: int
    items_scraped: int
    pages_crawled: int
    success_rate: float


class WorkerHistoryItem(BaseModel):
    timestamp: datetime
    tasks_total: int = 0
    tasks_success: int = 0
    tasks_failed: int = 0
    tasks_running: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: int = 0
    disk_percent: float = 0.0
    network_sent_mb: float = 0.0
    network_recv_mb: float = 0.0
    pages_crawled: int = 0
    items_scraped: int = 0
    requests_total: int = 0
    requests_failed: int = 0
    avg_response_time_ms: int = 0
    error_timeout: int = 0
    error_network: int = 0
    error_parse: int = 0
    error_other: int = 0


class WorkerHistoryQueryResponse(BaseModel):
    worker_id: str
    metric_type: str
    data: list[WorkerHistoryItem]
    count: int
