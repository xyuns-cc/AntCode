"""节点相关的 Pydantic 模式定义"""

from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, ConfigDict, Field


class DrissionPageCapability(BaseModel):
    """DrissionPage 渲染能力"""
    enabled: bool = Field(False, description="是否启用")
    browser_path: Optional[str] = Field(None, description="浏览器路径")
    headless: bool = Field(True, description="是否无头模式")
    max_instances: int = Field(1, ge=1, description="最大浏览器实例数")
    window_size: str = Field("1920,1080", description="窗口大小")
    page_load_timeout: int = Field(30, description="页面加载超时(秒)")


class CurlCffiCapability(BaseModel):
    """curl_cffi 能力"""
    enabled: bool = Field(False, description="是否启用")
    default_impersonate: str = Field("chrome120", description="默认模拟浏览器")


class NodeCapabilities(BaseModel):
    """节点能力配置"""
    drissionpage: Optional[Dict[str, Any]] = Field(None, description="DrissionPage 渲染能力")
    curl_cffi: Optional[Dict[str, Any]] = Field(None, description="curl_cffi 能力")

    model_config = {"extra": "ignore"}

    def has_render_capability(self) -> bool:
        """检查是否有渲染能力"""
        return bool(self.drissionpage and self.drissionpage.get("enabled"))


class NodeMetrics(BaseModel):
    """节点指标"""
    cpu: float = Field(0, ge=0, le=100, description="CPU 使用率")
    memory: float = Field(0, ge=0, le=100, description="内存使用率")
    disk: float = Field(0, ge=0, le=100, description="磁盘使用率")
    taskCount: int = Field(0, ge=0, description="任务总数")
    runningTasks: int = Field(0, ge=0, description="运行中任务数")
    maxConcurrentTasks: int = Field(5, ge=1, description="最大并发任务数")
    projectCount: int = Field(0, ge=0, description="项目总数")
    envCount: int = Field(0, ge=0, description="环境总数")
    uptime: int = Field(0, ge=0, description="运行时间（秒）")
    # 详细资源信息
    cpuCores: Optional[int] = Field(None, description="CPU 核心数")
    memoryTotal: Optional[int] = Field(None, description="总内存 (bytes)")
    memoryUsed: Optional[int] = Field(None, description="已用内存 (bytes)")
    memoryAvailable: Optional[int] = Field(None, description="可用内存 (bytes)")
    diskTotal: Optional[int] = Field(None, description="总磁盘 (bytes)")
    diskUsed: Optional[int] = Field(None, description="已用磁盘 (bytes)")
    diskFree: Optional[int] = Field(None, description="可用磁盘 (bytes)")


class NodeCreateRequest(BaseModel):
    """节点创建请求（手动添加）"""
    name: str = Field(..., min_length=1, max_length=100, description="节点名称")
    host: str = Field(..., min_length=1, max_length=255, description="主机地址")
    port: int = Field(8000, ge=1, le=65535, description="端口号")
    region: Optional[str] = Field(None, max_length=50, description="区域")
    description: Optional[str] = Field(None, max_length=500, description="描述")
    tags: Optional[List[str]] = Field(None, description="标签")


class NodeConnectRequest(BaseModel):
    """节点连接请求（通过地址和机器码添加）"""
    host: str = Field(..., min_length=1, max_length=255, description="节点地址")
    port: int = Field(..., ge=1, le=65535, description="节点端口")
    machine_code: str = Field(..., min_length=1, max_length=32, description="机器码")


class NodeUpdateRequest(BaseModel):
    """节点更新请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="节点名称")
    host: Optional[str] = Field(None, min_length=1, max_length=255, description="主机地址")
    port: Optional[int] = Field(None, ge=1, le=65535, description="端口号")
    region: Optional[str] = Field(None, max_length=50, description="区域")
    description: Optional[str] = Field(None, max_length=500, description="描述")
    tags: Optional[List[str]] = Field(None, description="标签")
    status: Optional[str] = Field(None, description="状态")


class NodeRebindRequest(BaseModel):
    """节点重新绑定请求 - 用于更新机器码"""
    new_machine_code: str = Field(..., min_length=1, max_length=32, description="新的机器码")
    verify_connection: bool = Field(True, description="是否验证连接（验证新机器码是否匹配）")


class NodeResponse(BaseModel):
    """节点响应"""
    id: str = Field(..., description="节点公开ID")
    name: str = Field(..., description="节点名称")
    host: str = Field(..., description="主机地址")
    port: int = Field(..., description="端口号")
    status: str = Field(..., description="节点状态")
    region: Optional[str] = Field(None, description="区域")
    description: Optional[str] = Field(None, description="描述")
    tags: Optional[List[str]] = Field(None, description="标签")
    version: Optional[str] = Field(None, description="版本")

    # 操作系统信息
    osType: Optional[str] = Field(None, description="操作系统类型: Windows/Linux/Darwin")
    osVersion: Optional[str] = Field(None, description="操作系统版本")
    pythonVersion: Optional[str] = Field(None, description="Python 版本")
    machineArch: Optional[str] = Field(None, description="CPU 架构: x86_64/arm64")

    # 节点能力
    capabilities: Optional[NodeCapabilities] = Field(None, description="节点能力配置")
    hasRenderCapability: bool = Field(False, description="是否有渲染能力")

    metrics: Optional[NodeMetrics] = Field(None, description="节点指标")
    lastHeartbeat: Optional[datetime] = Field(None, description="最后心跳时间")
    createdAt: datetime = Field(..., description="创建时间")
    updatedAt: Optional[datetime] = Field(None, description="更新时间")

    model_config = ConfigDict(from_attributes=True)


class NodeListResponse(BaseModel):
    """节点列表响应"""
    items: List[NodeResponse] = Field(..., description="节点列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页")
    size: int = Field(..., description="每页数量")


class NodeAggregateStats(BaseModel):
    """节点聚合统计"""
    totalNodes: int = Field(0, description="总节点数")
    onlineNodes: int = Field(0, description="在线节点数")
    offlineNodes: int = Field(0, description="离线节点数")
    maintenanceNodes: int = Field(0, description="维护中节点数")
    totalProjects: int = Field(0, description="总项目数")
    totalTasks: int = Field(0, description="总任务数")
    runningTasks: int = Field(0, description="运行中任务数")
    totalEnvs: int = Field(0, description="总环境数")
    avgCpu: float = Field(0, description="平均CPU使用率")
    avgMemory: float = Field(0, description="平均内存使用率")


class NodeHeartbeatRequest(BaseModel):
    """节点心跳请求"""
    node_id: str = Field(..., description="节点ID")
    api_key: str = Field(..., description="API密钥")
    status: str = Field("online", description="节点状态")
    metrics: Optional[NodeMetrics] = Field(None, description="节点指标")
    version: Optional[str] = Field(None, description="版本")

    # 操作系统信息（首次上报或变更时发送）
    os_type: Optional[str] = Field(None, description="操作系统类型: Windows/Linux/Darwin")
    os_version: Optional[str] = Field(None, description="操作系统版本")
    python_version: Optional[str] = Field(None, description="Python 版本")
    machine_arch: Optional[str] = Field(None, description="CPU 架构")

    # 节点能力（首次上报或变更时发送）
    capabilities: Optional[NodeCapabilities] = Field(None, description="节点能力配置")


class NodeTestConnectionResponse(BaseModel):
    """测试连接响应"""
    success: bool = Field(..., description="是否成功")
    latency: Optional[int] = Field(None, description="延迟（毫秒）")
    error: Optional[str] = Field(None, description="错误信息")


class NodeRegisterRequest(BaseModel):
    """节点注册请求（节点主动注册）"""
    name: str = Field(..., min_length=1, max_length=100, description="节点名称")
    host: str = Field(..., min_length=1, max_length=255, description="主机地址")
    port: int = Field(8000, ge=1, le=65535, description="端口号")
    region: Optional[str] = Field(None, max_length=50, description="区域")
    version: Optional[str] = Field(None, description="版本")
    metrics: Optional[NodeMetrics] = Field(None, description="初始指标")


class NodeRegisterResponse(BaseModel):
    """节点注册响应"""
    id: str = Field(..., description="节点公开ID")
    api_key: str = Field(..., description="API密钥")
    secret_key: str = Field(..., description="密钥")

