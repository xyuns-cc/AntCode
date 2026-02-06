"""系统配置相关Schema"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SystemConfigBase(BaseModel):
    """系统配置基础Schema"""

    config_key: str = Field(..., description="配置键")
    config_value: str = Field(..., description="配置值（JSON字符串）")
    category: str = Field(..., description="配置分类")
    description: str = Field("", description="配置描述")
    value_type: str = Field(default="string", description="数据类型")
    is_active: bool = Field(default=True, description="是否启用")


class SystemConfigCreate(SystemConfigBase):
    """创建系统配置Schema"""

    pass


class SystemConfigUpdate(BaseModel):
    """更新系统配置Schema"""

    config_value: str | None = Field(None, description="配置值")
    description: str | None = Field(None, description="配置描述")
    is_active: bool | None = Field(None, description="是否启用")


class SystemConfigResponse(SystemConfigBase):
    """系统配置响应Schema"""

    modified_by: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SystemConfigBatchUpdate(BaseModel):
    """批量更新配置Schema"""

    configs: list[dict[str, Any]] = Field(..., description="配置列表")


class TaskResourceConfig(BaseModel):
    """任务资源配置"""

    max_concurrent_tasks: int = Field(default=10, ge=1, le=100, description="最大并发任务数")
    task_execution_timeout: int = Field(
        default=3600, ge=60, le=86400, description="任务执行超时时间（秒）"
    )
    task_cpu_time_limit: int = Field(
        default=600, ge=60, le=3600, description="任务CPU时间限制（秒）"
    )
    task_memory_limit: int = Field(default=1024, ge=128, le=8192, description="任务内存限制（MB）")
    task_max_retries: int = Field(default=3, ge=0, le=10, description="任务最大重试次数")
    task_retry_delay: int = Field(default=60, ge=10, le=600, description="任务重试延迟（秒）")


class TaskLogConfig(BaseModel):
    """任务日志配置"""

    task_log_retention_days: int = Field(default=30, ge=1, le=365, description="日志保留天数")
    task_log_max_size: int = Field(
        default=104857600, ge=1048576, le=1073741824, description="日志最大大小（字节）"
    )


class SchedulerConfig(BaseModel):
    """调度器配置"""

    scheduler_timezone: str = Field(default="Asia/Shanghai", description="调度器时区")
    cleanup_workspace_on_completion: bool = Field(default=True, description="完成后清理工作空间")
    cleanup_workspace_max_age_hours: int = Field(
        default=24, ge=1, le=168, description="工作空间最大保留时间（小时）"
    )


class CacheConfig(BaseModel):
    """缓存配置"""

    cache_enabled: bool = Field(default=True, description="是否启用缓存")
    cache_default_ttl: int = Field(default=300, ge=60, le=3600, description="默认缓存TTL（秒）")
    metrics_cache_ttl: int = Field(default=30, ge=10, le=300, description="指标缓存TTL（秒）")
    api_cache_ttl: int = Field(default=300, ge=60, le=3600, description="API缓存TTL（秒）")
    users_cache_ttl: int = Field(default=300, ge=60, le=3600, description="用户缓存TTL（秒）")
    query_cache_ttl: int = Field(default=300, ge=60, le=3600, description="查询缓存TTL（秒）")
    metrics_background_update: bool = Field(default=True, description="是否启用指标后台更新")
    metrics_update_interval: int = Field(default=15, ge=5, le=300, description="指标更新间隔（秒）")


class MonitoringConfig(BaseModel):
    """监控配置"""

    monitoring_enabled: bool = Field(default=True, description="是否启用监控")
    monitor_status_ttl: int = Field(default=300, ge=60, le=3600, description="监控状态TTL（秒）")
    monitor_history_ttl: int = Field(
        default=3600, ge=600, le=86400, description="监控历史TTL（秒）"
    )
    monitor_history_keep_days: int = Field(default=30, ge=1, le=365, description="监控历史保留天数")
    monitor_cluster_ttl: int = Field(default=300, ge=60, le=3600, description="集群状态TTL（秒）")
    monitor_stream_batch_size: int = Field(
        default=100, ge=10, le=1000, description="监控流批处理大小"
    )
    monitor_stream_interval: int = Field(
        default=120, ge=30, le=600, description="监控流处理间隔（秒）"
    )
    monitor_stream_maxlen: int = Field(
        default=10000, ge=1000, le=100000, description="监控流最大长度"
    )


class BrandingConfig(BaseModel):
    """品牌配置"""

    brand_name: str = Field(default="AntCode", description="品牌名称")
    app_title: str = Field(default="AntCode 任务调度平台", description="应用标题")
    logo_text: str = Field(default="AntCode", description="Logo 展示名称")
    logo_short: str = Field(default="A", description="Logo 简写")
    logo_icon: str = Field(default="RocketOutlined", description="Logo 图标（Ant Design 名称）")
    logo_url: str | None = Field(default=None, description="Logo 图片 URL")
    favicon_url: str | None = Field(default=None, description="浏览器图标 URL")


class AllSystemConfigs(BaseModel):
    """所有系统配置"""

    task_resource: TaskResourceConfig
    task_log: TaskLogConfig
    scheduler: SchedulerConfig
    cache: CacheConfig
    monitoring: MonitoringConfig
