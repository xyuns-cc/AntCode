"""节点模型 - 分布式工作节点"""

from tortoise import fields

from src.models.base import BaseModel


class NodeStatus:
    """节点状态"""
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    CONNECTING = "connecting"


class Node(BaseModel):
    """工作节点模型"""
    # 注意: public_id 已在 BaseModel 中定义，无需重复定义
    name = fields.CharField(max_length=100, unique=True)
    host = fields.CharField(max_length=255)
    port = fields.IntField(default=8000)
    status = fields.CharField(max_length=20, default=NodeStatus.OFFLINE)

    # 节点信息
    region = fields.CharField(max_length=50, null=True)
    description = fields.TextField(null=True)
    tags = fields.JSONField(default=list)
    version = fields.CharField(max_length=50, null=True)

    # 操作系统信息（由节点上报）
    os_type = fields.CharField(max_length=20, null=True, description="操作系统类型: Windows/Linux/Darwin")
    os_version = fields.CharField(max_length=100, null=True, description="操作系统版本")
    python_version = fields.CharField(max_length=20, null=True, description="Python 版本")
    machine_arch = fields.CharField(max_length=20, null=True, description="CPU 架构: x86_64/arm64")

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
    metrics = fields.JSONField(null=True)
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
        table = "nodes"
        indexes = [
            ("name",),
            ("host", "port"),
            ("status",),
            ("region",),
        ]

    @property
    def machine_code(self):
        if isinstance(self.metrics, dict):
            return self.metrics.get("machine_code")
        return None

    @machine_code.setter
    def machine_code(self, value):
        if not isinstance(self.metrics, dict):
            self.metrics = {}
        self.metrics["machine_code"] = value


class NodeHeartbeat(BaseModel):
    """节点心跳记录"""
    node_id = fields.BigIntField()
    metrics = fields.JSONField(null=True)
    status = fields.CharField(max_length=20)
    timestamp = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "node_heartbeats"
        indexes = [
            ("node_id",),
            ("timestamp",),
        ]


class UserNodePermission(BaseModel):
    """用户节点权限 - 记录用户可以访问哪些节点"""
    user_id = fields.BigIntField(db_index=True, description="用户ID")
    node_id = fields.BigIntField(db_index=True, description="节点ID")

    # 权限级别：view-只读查看, use-可以在节点上创建项目/环境/任务
    permission = fields.CharField(max_length=20, default="use", description="权限级别")

    # 分配信息
    assigned_by = fields.BigIntField(null=True, description="分配者ID（管理员）")
    assigned_at = fields.DatetimeField(auto_now_add=True, description="分配时间")

    # 备注
    note = fields.TextField(null=True, description="备注说明")

    class Meta:
        table = "user_node_permissions"
        unique_together = [("user_id", "node_id")]
        indexes = [
            ("user_id",),
            ("node_id",),
        ]

    def __str__(self):
        return f"User({self.user_id}) -> Node({self.node_id}): {self.permission}"
