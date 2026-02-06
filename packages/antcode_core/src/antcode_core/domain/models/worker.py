"""
Worker 节点模型

分布式工作节点的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel
from antcode_core.domain.models.enums import WorkerStatus


class Worker(BaseModel):
    """Worker 节点模型

    表示一个分布式工作节点，负责执行爬虫任务。
    """

    name = fields.CharField(max_length=100, unique=True)
    host = fields.CharField(max_length=255)
    port = fields.IntField(default=8001)
    status = fields.CharField(max_length=20, default=WorkerStatus.OFFLINE.value)

    # 节点信息
    region = fields.CharField(max_length=50, null=True)
    description = fields.TextField(null=True)
    tags = fields.JSONField(default=list)
    version = fields.CharField(max_length=50, null=True)

    # 操作系统信息（由节点上报）
    os_type = fields.CharField(
        max_length=20, null=True, description="操作系统类型: Windows/Linux/Darwin"
    )
    os_version = fields.CharField(max_length=100, null=True, description="操作系统版本")
    python_version = fields.CharField(max_length=20, null=True, description="Python 版本")
    machine_arch = fields.CharField(max_length=20, null=True, description="CPU 架构: x86_64/arm64")

    # 连接模式：direct（直连Redis）或 gateway（通过网关）
    transport_mode = fields.CharField(
        max_length=20, default="gateway", description="连接模式: direct/gateway"
    )

    # 节点能力（由节点心跳上报）
    # capabilities 结构: {
    #   "drissionpage": {
    #     "enabled": true,
    #     "browser_path": "/usr/bin/chromium",
    #     "headless": true,
    #     "max_instances": 3
    #   },
    #   "curl_cffi": {"enabled": true},
    #   "playwright": {"enabled": false},
    #   "selenium": {"enabled": false}
    # }
    capabilities = fields.JSONField(null=True, default=dict, description="节点能力配置")

    # 资源限制配置（由主控下发）
    # resource_limits 结构: {
    #   "cpu_time": 3600,        # CPU 时间限制 (秒)
    #   "wall_time": 7200,       # 墙钟时间限制 (秒)
    #   "memory_mb": 512,        # 内存限制 (MB)
    #   "disk_mb": 1024,         # 磁盘写入限制 (MB)
    #   "file_size_mb": 100,     # 单文件大小限制 (MB)
    #   "max_processes": 50,     # 最大进程数
    #   "max_open_files": 1024,  # 最大打开文件数
    #   "max_output_lines": 100000,  # 最大输出行数
    #   "enable_security_scan": true  # 是否启用安全扫描
    # }
    resource_limits = fields.JSONField(null=True, default=dict, description="资源限制配置")

    # 节点指标（由节点定期上报）
    # metrics 结构: {
    #   "cpu": 45.5,
    #   "memory": 60.2,
    #   "disk": 30.0,
    #   "taskCount": 10,
    #   "runningTasks": 3,
    #   "projectCount": 5,
    #   "envCount": 2,
    #   "uptime": 86400,
    #   "maxConcurrentTasks": 5
    # }
    metrics = fields.JSONField(null=True)

    # 认证信息
    api_key = fields.CharField(max_length=64, null=True)
    secret_key = fields.CharField(max_length=128, null=True)

    # 时间戳
    last_heartbeat = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    # 创建者
    created_by = fields.BigIntField(null=True)

    def __str__(self):
        return f"{self.name} ({self.host}:{self.port})"

    class Meta:
        table = "workers"
        indexes = [
            ("name",),
            ("host", "port"),
            ("status",),
            ("region",),
        ]


class WorkerHeartbeat(BaseModel):
    """Worker 心跳记录"""

    worker_id = fields.BigIntField()
    metrics = fields.JSONField(null=True)
    status = fields.CharField(max_length=20)
    timestamp = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "worker_heartbeats"
        indexes = [
            ("worker_id",),
            ("timestamp",),
        ]


class UserWorkerPermission(BaseModel):
    """用户 Worker 权限

    记录用户可以访问哪些 Worker 节点。
    """

    user_id = fields.BigIntField(db_index=True, description="用户ID")
    worker_id = fields.BigIntField(db_index=True, description="Worker ID")

    # 权限级别：view-只读查看, use-可以在节点上创建项目/环境/任务
    permission = fields.CharField(max_length=20, default="use", description="权限级别")

    # 分配信息
    assigned_by = fields.BigIntField(null=True, description="分配者ID（管理员）")
    assigned_at = fields.DatetimeField(auto_now_add=True, description="分配时间")

    # 备注
    note = fields.TextField(null=True, description="备注说明")

    class Meta:
        table = "user_worker_permissions"
        unique_together = [("user_id", "worker_id")]
        indexes = [
            ("user_id",),
            ("worker_id",),
        ]

    def __str__(self):
        return f"User({self.user_id}) -> Worker({self.worker_id}): {self.permission}"


__all__ = [
    "Worker",
    "WorkerHeartbeat",
    "UserWorkerPermission",
]
