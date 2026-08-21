"""
Worker Schema

Worker 节点相关的请求和响应模式。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from antcode_core.domain.models.enums import WorkerStatus
from antcode_core.domain.schemas.worker_registration import (
    WorkerInstallKeyListItem,
    WorkerInstallKeyRequest,
    WorkerInstallKeyResponse,
    WorkerRegisterByKeyV2Request,
    WorkerRegisterByKeyV2Response,
    WorkerRegistrationAckRequest,
)


class WorkerCapabilities(BaseModel):
    """Worker 能力配置"""

    curl_cffi: dict[str, Any] = Field(default_factory=dict, description="curl_cffi 能力")
    # T6-T4a fixup: worker 侧的 CapabilityDetector 上报 task_types
    # (list of plugin names: code/rule/spider/render 等)，供 master
    # dispatcher.select_best_worker 按 capability 过滤。schema 之前用
    # extra=forbid 会拒绝这个字段导致 direct 注册 400 Bad Request。
    task_types: list[str] = Field(default_factory=list, description="worker 已装载的 plugin/task type 列表")
    # 下面两项同样出自 Worker 的能力快照（services/worker/.../app/capabilities.py），
    # 且控制面自己就在用：workers_query 按 playwright.enabled 过滤渲染节点，
    # wire_contract 决定 Lease 发不发。本模型是 extra="forbid"，漏掉任意一项都不是
    # "少显示一个字段"，而是整份 capabilities 校验失败。
    playwright: dict[str, Any] = Field(default_factory=dict, description="playwright 渲染能力")
    wire_contract: int | None = Field(default=None, description="Worker 声明的线协议契约版本")

    model_config = ConfigDict(extra="forbid")


class WorkerMetrics(BaseModel):
    """Worker 指标"""

    cpu: float = Field(default=0, ge=0, le=100, description="CPU 使用率")
    memory: float = Field(default=0, ge=0, le=100, description="内存使用率")
    disk: float = Field(default=0, ge=0, le=100, description="磁盘使用率")
    taskCount: int = Field(default=0, ge=0, description="任务总数")
    runningTasks: int = Field(default=0, ge=0, description="运行中任务数")
    queuedTasks: int = Field(default=0, ge=0, description="排队任务数")
    maxConcurrentTasks: int = Field(default=5, ge=1, description="最大并发任务数")
    projectCount: int = Field(default=0, ge=0, description="项目总数")
    envCount: int = Field(default=0, ge=0, description="环境总数")
    uptime: int = Field(default=0, ge=0, description="运行时间（秒）")
    cpuCores: int = Field(default=0, ge=0, description="CPU 核心数")
    memoryTotal: int = Field(default=0, ge=0, description="总内存 (bytes)")
    memoryUsed: int = Field(default=0, ge=0, description="已用内存 (bytes)")
    memoryAvailable: int = Field(default=0, ge=0, description="可用内存 (bytes)")
    diskTotal: int = Field(default=0, ge=0, description="总磁盘 (bytes)")
    diskUsed: int = Field(default=0, ge=0, description="已用磁盘 (bytes)")
    diskFree: int = Field(default=0, ge=0, description="可用磁盘 (bytes)")
    # 生效单任务限额；0 = 没有限额在生效，故 ge=0 而不是 ge=1。
    taskMemoryLimitMb: int = Field(default=0, ge=0, description="生效单任务内存限额 (MB)")
    taskCpuTimeLimitSec: int = Field(default=0, ge=0, description="生效单任务 CPU 时间限额 (秒)")
    # 心跳把爬虫统计**并进** metrics 落库（worker_heartbeat_persistence 的
    # build_worker_heartbeat_update / _redis_metrics），spider_stats_service 与
    # worker_stats_service 也都按 metrics["spider_stats"] 读，这就是持久化契约。
    # 读回 schema 必须认识它。原样透传 dict 而不套一层子模型：这里是展示面，口径
    # 归上述两个服务；再加一个 forbid 子模型只是把同一个陷阱下移一层。
    spider_stats: dict[str, Any] = Field(default_factory=dict, description="心跳携带的爬虫统计摘要")

    model_config = ConfigDict(extra="forbid")


class WorkerCreateRequest(BaseModel):
    """Worker 创建请求"""

    name: str = Field(..., min_length=1, max_length=100, description="Worker 名称")
    host: str = Field(..., min_length=1, max_length=255, description="主机地址")
    port: int = Field(8001, ge=1, le=65535, description="节点端口")
    region: str = Field("", max_length=50, description="区域")
    description: str = Field("", max_length=500, description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")


class WorkerUpdateRequest(BaseModel):
    """Worker 更新请求"""

    name: str = Field("", min_length=0, max_length=100, description="Worker 名称")
    host: str = Field("", min_length=0, max_length=255, description="主机地址")
    port: int = Field(0, ge=0, le=65535, description="端口号")
    region: str = Field("", max_length=50, description="区域")
    description: str = Field("", max_length=500, description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")
    status: WorkerStatus | None = Field(None, description="状态")


class WorkerResponse(BaseModel):
    """Worker 响应"""

    id: str = Field(..., description="Worker 公开ID")
    name: str = Field(..., description="Worker 名称")
    host: str = Field(..., description="主机地址")
    port: int = Field(..., description="端口号")
    status: str = Field(..., description="Worker 状态")
    region: str = Field("", description="区域")
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")
    version: str = Field("", description="版本")

    osType: str = Field("", description="操作系统类型")
    osVersion: str = Field("", description="操作系统版本")
    pythonVersion: str = Field("", description="Python 版本")
    machineArch: str = Field("", description="CPU 架构")

    transportMode: str | None = Field(None, description="连接模式: direct/gateway")

    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)
    metrics: WorkerMetrics = Field(default_factory=WorkerMetrics)
    lastHeartbeat: str = Field("", description="最后心跳时间")
    createdAt: datetime = Field(..., description="创建时间")
    updatedAt: str = Field("", description="更新时间")

    model_config = ConfigDict(from_attributes=True)


class WorkerListResponse(BaseModel):
    """Worker 列表响应"""

    items: list[WorkerResponse] = Field(..., description="Worker 列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页")
    size: int = Field(..., description="每页数量")


class WorkerAggregateStats(BaseModel):
    """Worker 聚合统计"""

    totalWorkers: int = Field(0, description="总 Worker 数")
    onlineWorkers: int = Field(0, description="在线 Worker 数")
    offlineWorkers: int = Field(0, description="离线 Worker 数")
    maintenanceWorkers: int = Field(0, description="维护中 Worker 数")
    totalProjects: int = Field(0, description="总项目数")
    totalTasks: int = Field(0, description="总任务数")
    runningTasks: int = Field(0, description="运行中任务数")
    totalEnvs: int = Field(0, description="总环境数")
    avgCpu: float = Field(0, description="平均CPU使用率")
    avgMemory: float = Field(0, description="平均内存使用率")

    totalRequests: int = Field(0, description="集群总请求数")
    totalResponses: int = Field(0, description="集群总响应数")
    totalItemsScraped: int = Field(0, description="集群总抓取数据项")
    totalErrors: int = Field(0, description="集群总错误数")
    avgLatencyMs: float = Field(0.0, description="集群平均延迟(毫秒)")
    clusterRequestsPerMinute: float = Field(0.0, description="集群每分钟请求数")


class SpiderStatsSummary(BaseModel):
    """爬虫统计摘要 - 用于心跳上报和 API 响应"""

    requestCount: int = Field(default=0, ge=0, description="请求总数")
    responseCount: int = Field(default=0, ge=0, description="响应总数")
    itemScrapedCount: int = Field(default=0, ge=0, description="抓取数据项数")
    errorCount: int = Field(default=0, ge=0, description="错误总数")
    avgLatencyMs: float = Field(default=0.0, ge=0, description="平均延迟(毫秒)")
    requestsPerMinute: float = Field(default=0.0, ge=0, description="每分钟请求数")
    statusCodes: dict[str, int] = Field(default_factory=dict, description="状态码分布")

    @classmethod
    def from_heartbeat(cls, data: dict | None) -> "SpiderStatsSummary":
        """从心跳数据构建"""
        if not data:
            return cls()
        return cls(
            requestCount=data.get("request_count", 0),
            responseCount=data.get("response_count", 0),
            itemScrapedCount=data.get("item_scraped_count", 0),
            errorCount=data.get("error_count", 0),
            avgLatencyMs=data.get("avg_latency_ms", 0.0),
            requestsPerMinute=data.get("requests_per_minute", 0.0),
            statusCodes=data.get("status_codes", {}),
        )


class WorkerHeartbeatRequest(BaseModel):
    """Worker 心跳请求"""

    worker_id: str = Field(..., description="Worker ID")
    api_key: str = Field(..., description="API密钥")
    status: str = Field("online", description="Worker 状态")
    metrics: WorkerMetrics = Field(default_factory=WorkerMetrics)
    version: str = Field("", description="版本")

    os_type: str = Field("", description="操作系统类型")
    os_version: str = Field("", description="操作系统版本")
    python_version: str = Field("", description="Python 版本")
    machine_arch: str = Field("", description="CPU 架构")

    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)
    spider_stats: dict = Field(default_factory=dict, description="爬虫统计摘要")


class WorkerTestConnectionResponse(BaseModel):
    """测试连接响应"""

    success: bool = Field(..., description="是否成功")
    latency: int = Field(0, description="延迟（毫秒）")
    error: str = Field("", description="错误信息")
    connection_type: str = Field("", description="连接类型（heartbeat/gateway/direct）")


class WorkerRegisterRequest(BaseModel):
    """Worker 注册请求（Worker 主动注册）"""

    name: str = Field(..., min_length=1, max_length=100, description="Worker 名称")
    host: str = Field(..., min_length=1, max_length=255, description="主机地址")
    port: int = Field(8001, ge=1, le=65535, description="节点端口")
    region: str = Field("", max_length=50, description="区域")
    version: str = Field("", description="版本")
    metrics: WorkerMetrics = Field(default_factory=WorkerMetrics, description="初始指标")


class WorkerRegisterResponse(BaseModel):
    """Worker 注册响应"""

    worker_id: str = Field(..., description="Worker 公开ID")
    api_key: str = Field(..., description="API密钥")
    secret_key: str = Field(..., description="密钥")


class WorkerRegisterDirectRequest(BaseModel):
    """Direct 模式注册请求"""

    worker_id: str = Field(
        ...,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Worker ID",
    )
    proof: str = Field(..., min_length=1, description="Direct 注册证明")
    name: str = Field("", max_length=100, description="Worker 名称")
    host: str = Field("", max_length=255, description="主机地址")
    port: int = Field(8001, ge=1, le=65535, description="端口")
    region: str = Field("", max_length=50, description="区域")
    version: str = Field("", description="版本")
    os_type: str = Field("", description="操作系统类型")
    os_version: str = Field("", description="操作系统版本")
    python_version: str = Field("", description="Python 版本")
    machine_arch: str = Field("", description="CPU 架构")
    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)


class WorkerRegisterDirectResponse(BaseModel):
    """Direct 模式注册响应"""

    worker_id: str = Field(..., description="Worker 公开ID")
    created: bool = Field(False, description="是否新建")
    redis_username: str | None = Field(
        default=None,
        description="Worker 专属 Redis ACL 用户名（开启 REDIS_ACL 时返回）",
    )
    redis_password: str | None = Field(
        default=None,
        description="Worker 专属 Redis ACL 密码（明文，仅此一次）",
    )


__all__ = [
    "WorkerCapabilities",
    "WorkerMetrics",
    "WorkerCreateRequest",
    "WorkerUpdateRequest",
    "WorkerResponse",
    "WorkerListResponse",
    "WorkerAggregateStats",
    "SpiderStatsSummary",
    "WorkerHeartbeatRequest",
    "WorkerTestConnectionResponse",
    "WorkerRegisterRequest",
    "WorkerRegisterResponse",
    "WorkerRegisterDirectRequest",
    "WorkerRegisterDirectResponse",
    "WorkerInstallKeyRequest",
    "WorkerInstallKeyResponse",
    "WorkerInstallKeyListItem",
    "WorkerRegisterByKeyV2Request",
    "WorkerRegisterByKeyV2Response",
    "WorkerRegistrationAckRequest",
]
