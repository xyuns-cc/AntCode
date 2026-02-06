"""爬取批次相关的 Pydantic 模式定义"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# 请求模型
# =============================================================================


class CrawlBatchCreateRequest(BaseModel):
    """创建爬取批次请求"""

    project_id: str = Field(..., description="项目公开ID")
    name: str = Field(..., min_length=1, max_length=255, description="批次名称")
    description: str = Field("", max_length=1000, description="批次描述")
    seed_urls: list[str] = Field(..., min_length=1, description="种子URL列表")
    max_depth: int = Field(3, ge=1, le=10, description="最大爬取深度")
    max_pages: int = Field(10000, ge=1, le=1000000, description="最大爬取页面数")
    max_concurrency: int = Field(50, ge=1, le=500, description="最大并发数")
    request_delay: float = Field(0.5, ge=0, le=60, description="请求间隔(秒)")
    timeout: int = Field(30, ge=1, le=300, description="请求超时(秒)")
    max_retries: int = Field(3, ge=0, le=10, description="最大重试次数")


class CrawlBatchTestRequest(BaseModel):
    """测试执行请求"""

    project_id: str = Field(..., description="项目公开ID")
    seed_urls: list[str] = Field(..., min_length=1, max_length=10, description="种子URL列表")
    max_depth: int = Field(2, ge=1, le=3, description="最大爬取深度")
    max_pages: int = Field(10, ge=1, le=100, description="最大爬取页面数")


# =============================================================================
# 响应模型
# =============================================================================


class CrawlBatchResponse(BaseModel):
    """爬取批次响应"""

    id: str = Field(..., description="批次公开ID")
    project_id: str = Field(..., description="项目公开ID")
    name: str = Field(..., description="批次名称")
    description: str = Field("", description="批次描述")
    seed_urls: list[str] = Field(..., description="种子URL列表")
    max_depth: int = Field(..., description="最大爬取深度")
    max_pages: int = Field(..., description="最大爬取页面数")
    max_concurrency: int = Field(..., description="最大并发数")
    request_delay: float = Field(..., description="请求间隔(秒)")
    timeout: int = Field(..., description="请求超时(秒)")
    max_retries: int = Field(..., description="最大重试次数")
    status: str = Field(..., description="批次状态")
    is_test: bool = Field(..., description="是否为测试批次")
    created_at: datetime = Field(..., description="创建时间")
    started_at: str = Field("", description="开始时间")
    completed_at: str = Field("", description="完成时间")

    model_config = ConfigDict(from_attributes=True)


class BatchProgressResponse(BaseModel):
    """批次进度响应"""

    batch_id: str = Field(..., description="批次公开ID")
    total_urls: int = Field(0, description="总URL数")
    pending_urls: int = Field(0, description="待处理数")
    completed_urls: int = Field(0, description="已完成数")
    failed_urls: int = Field(0, description="失败数")
    active_workers: int = Field(0, description="活跃Worker数")
    speed_per_minute: float = Field(0.0, description="速度(URLs/分钟)")
    last_updated: str = Field("", description="最后更新时间")


class CrawlMetricsResponse(BaseModel):
    """爬取监控指标响应"""

    stream_length: int = Field(0, description="Stream队列长度")
    pel_size: int = Field(0, description="PEL待确认消息数")
    dedup_size: int = Field(0, description="去重集合大小")
    dead_letter_count: int = Field(0, description="死信队列消息数")
    active_workers: int = Field(0, description="活跃Worker数")
    total_batches: int = Field(0, description="总批次数")
    running_batches: int = Field(0, description="运行中批次数")


class CrawlTestResultResponse(BaseModel):
    """测试执行结果响应"""

    batch_id: str = Field(..., description="测试批次ID")
    success: bool = Field(..., description="是否成功")
    total_pages: int = Field(0, description="爬取页面数")
    success_pages: int = Field(0, description="成功页面数")
    failed_pages: int = Field(0, description="失败页面数")
    sample_data: list[dict] = Field(default_factory=list, description="样本数据")
    errors: list[str] = Field(default_factory=list, description="错误信息列表")
    duration_seconds: float = Field(0.0, description="执行耗时(秒)")


# =============================================================================
# 批次监控指标响应（嵌套结构，与 BatchMetrics.to_dict() 一致）
# =============================================================================


class BatchProgressInfo(BaseModel):
    """批次进度信息"""

    total_urls: int = Field(0, description="总URL数")
    completed_urls: int = Field(0, description="已完成数")
    failed_urls: int = Field(0, description="失败数")
    pending_urls: int = Field(0, description="待处理数")
    speed_per_minute: float = Field(0.0, description="速度(URLs/分钟)")
    active_workers: int = Field(0, description="活跃Worker数")


class BatchQueueInfo(BaseModel):
    """批次队列信息"""

    length: int = Field(0, description="队列长度")
    pel_size: int = Field(0, description="PEL待确认消息数")


class BatchConfigInfo(BaseModel):
    """批次配置信息"""

    max_depth: int = Field(0, description="最大爬取深度")
    max_pages: int = Field(0, description="最大爬取页面数")
    max_concurrency: int = Field(0, description="最大并发数")
    max_retries: int = Field(0, description="最大重试次数")


class BatchMetricsResponse(BaseModel):
    """批次监控指标响应（与 BatchMetrics.to_dict() 结构一致）"""

    batch_id: str = Field(..., description="批次ID")
    project_id: str = Field(..., description="项目ID")
    status: str = Field(..., description="批次状态")
    progress: BatchProgressInfo = Field(default_factory=BatchProgressInfo, description="进度信息")
    queue: BatchQueueInfo = Field(default_factory=BatchQueueInfo, description="队列信息")
    config: BatchConfigInfo = Field(default_factory=BatchConfigInfo, description="配置信息")
    collected_at: str = Field("", description="采集时间")


# =============================================================================
# 队列详细指标响应
# =============================================================================


class QueuePriorityStats(BaseModel):
    """单个优先级队列统计"""

    priority: int = Field(0, description="优先级")
    stream_length: int = Field(0, description="Stream长度")
    pending_count: int = Field(0, description="待处理数")
    consumers: dict = Field(default_factory=dict, description="消费者信息")


class QueueStatsInfo(BaseModel):
    """队列统计信息"""

    high: QueuePriorityStats | None = Field(None, description="高优先级队列")
    normal: QueuePriorityStats | None = Field(None, description="普通优先级队列")
    low: QueuePriorityStats | None = Field(None, description="低优先级队列")


class ConsumerStatsInfo(BaseModel):
    """消费者统计信息"""

    total_consumers: int = Field(0, description="消费者总数")
    active_workers: int = Field(0, description="活跃Worker数")


class QueueMetricsResponse(BaseModel):
    """队列详细指标响应"""

    project_id: str = Field(..., description="项目ID")
    queue_stats: dict = Field(default_factory=dict, description="队列统计")
    consumer_stats: dict = Field(default_factory=dict, description="消费者统计")


# =============================================================================
# 告警相关响应
# =============================================================================


class AlertInfo(BaseModel):
    """告警信息（与 Alert.to_dict() 结构一致）"""

    level: str = Field(..., description="告警级别(warning/critical)")
    metric_name: str = Field(..., description="指标名称")
    current_value: int = Field(0, description="当前值")
    threshold: int = Field(0, description="阈值")
    message: str = Field(..., description="告警消息")
    project_id: str = Field("", description="项目ID")
    created_at: str = Field("", description="告警时间")


class AlertsResponse(BaseModel):
    """告警检测响应"""

    project_id: str = Field(..., description="项目ID")
    alerts: list[AlertInfo] = Field(default_factory=list, description="告警列表")
    alert_count: int = Field(0, description="告警数量")
    has_critical_alerts: bool = Field(False, description="是否有严重告警")


class AlertConfigResponse(BaseModel):
    """告警配置响应"""

    stream_length_threshold: int = Field(100000, description="Stream长度告警阈值")
    pel_size_threshold: int = Field(10000, description="PEL大小告警阈值")
    dead_letter_threshold: int = Field(1000, description="死信队列告警阈值")
    dedup_size_threshold: int = Field(10000000, description="去重集合大小告警阈值")


# =============================================================================
# 系统指标汇总响应
# =============================================================================


class SystemMetricsInfo(BaseModel):
    """系统指标信息（与 SystemMetrics.to_dict() 结构一致）"""

    project_id: str = Field(..., description="项目ID")
    total_stream_length: int = Field(0, description="总Stream长度")
    total_pel_size: int = Field(0, description="总PEL大小")
    dead_letter_count: int = Field(0, description="死信队列消息数")
    dedup_size: int = Field(0, description="去重集合大小")
    active_workers: int = Field(0, description="活跃Worker数")
    total_consumers: int = Field(0, description="消费者总数")
    queues: dict = Field(default_factory=dict, description="各优先级队列指标")
    collected_at: str = Field("", description="采集时间")


class MetricsSummaryResponse(BaseModel):
    """指标汇总响应"""

    metrics: SystemMetricsInfo = Field(..., description="系统指标")
    alerts: list[AlertInfo] = Field(default_factory=list, description="告警列表")
    alert_count: int = Field(0, description="告警数量")
    has_critical_alerts: bool = Field(False, description="是否有严重告警")


# =============================================================================
# 测试状态响应
# =============================================================================


class TestStatusResponse(BaseModel):
    """测试状态响应"""

    batch_id: str = Field("", description="批次ID")
    status: str = Field(..., description="状态(pending/running/completed/failed/not_found)")
    progress: BatchProgressResponse | None = Field(None, description="进度信息")
    started_at: str | None = Field(None, description="开始时间")
    completed_at: str | None = Field(None, description="完成时间")


# =============================================================================
# 数据类（用于内部传输）
# =============================================================================


class CrawlTask(BaseModel):
    """爬取任务数据类"""

    msg_id: str = Field("", description="Stream消息ID")
    url: str = Field(..., description="目标URL")
    method: str = Field("GET", description="HTTP方法")
    headers: dict[str, str] = Field(default_factory=dict, description="请求头")
    depth: int = Field(0, description="当前深度")
    priority: int = Field(5, description="优先级(0=高, 5=普通, 9=低)")
    retry_count: int = Field(0, description="重试次数")
    parent_url: str = Field("", description="父URL")
    batch_id: str = Field("", description="批次ID")
    project_id: str = Field("", description="项目ID")


class CrawlResult(BaseModel):
    """爬取结果数据类"""

    msg_id: str = Field(..., description="消息ID")
    url: str = Field(..., description="URL")
    success: bool = Field(..., description="是否成功")
    status_code: int = Field(0, description="HTTP状态码")
    error: str = Field("", description="错误信息")
    data: dict = Field(default_factory=dict, description="提取的数据")
    new_urls: list[str] = Field(default_factory=list, description="新发现的URL")
