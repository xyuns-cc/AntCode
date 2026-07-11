"""
项目模型

项目及其详情的数据模型定义。
"""

from tortoise import fields

from antcode_core.common.security.encrypted_fields import EncryptedJSONField
from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import (
    CallbackType,
    CrawlEngine,
    ExecutionStrategy,
    ProjectStatus,
    ProjectType,
    RequestMethod,
    RuntimeKind,
    RuntimeScope,
)


class Project(BaseModel):
    """项目模型

    表示一个爬虫项目。
    """

    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    name = fields.CharField(max_length=255, unique=True)
    description = fields.TextField(null=True)
    type = fields.CharEnumField(ProjectType)
    status = fields.CharEnumField(ProjectStatus, default=ProjectStatus.ACTIVE)

    tags: fields.JSONField[list[str]] = fields.JSONField(default=list)
    dependencies: fields.JSONField[list[str] | None] = fields.JSONField(null=True)

    # ========== 环境相关字段 ==========
    env_location = fields.CharField(max_length=10, null=True, default="worker", description="环境位置，仅支持 worker")
    worker_id = fields.CharField(max_length=32, null=True, db_index=True)
    worker_env_name = fields.CharField(max_length=100, null=True)
    python_version = fields.CharField(max_length=20, null=True)
    runtime_scope = fields.CharEnumField(RuntimeScope, null=True)
    runtime_kind = fields.CharEnumField(RuntimeKind, null=True)
    runtime_locator = fields.CharField(max_length=500, null=True)
    current_runtime_id = fields.BigIntField(null=True)
    runtime_worker_id = fields.BigIntField(
        null=True,
        db_index=True,
        description="环境所在 Worker 节点内部 ID",
    )

    # ========== 执行策略相关字段 ==========
    execution_strategy = fields.CharEnumField(
        ExecutionStrategy,
        default=ExecutionStrategy.PREFER_BOUND,
        description="执行策略: fixed/specified/auto/prefer",
    )
    bound_worker_id = fields.BigIntField(null=True, db_index=True, description="绑定 Worker ID")

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    updated_by = fields.BigIntField(null=True)
    user_id = fields.BigIntField()

    # 统计
    star_count = fields.IntField(default=0)

    def __str__(self):
        return self.name

    class Meta:
        table = "projects"
        indexes = [
            ("name",),
            ("type",),
            ("status",),
            ("user_id",),
            ("env_location", "worker_id"),
            ("python_version",),
            ("runtime_scope",),
            ("runtime_kind",),
            ("runtime_locator",),
            ("created_at",),
            ("updated_at",),
            ("current_runtime_id",),
            ("type", "status"),
            ("user_id", "status"),
            ("status", "created_at"),
            ("status", "updated_at"),
            ("public_id",),
            ("execution_strategy",),
            ("bound_worker_id",),
            ("runtime_worker_id",),
        ]


class ProjectFile(BaseModel):
    """Git 文件项目详情。"""

    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    project_id = fields.BigIntField(unique=True)

    # M8：文件项目也支持非 Python 语言（如 Node/Go/Java 单文件脚本或 jar）
    language = fields.CharField(max_length=50, default="python")

    entry_point = fields.CharField(max_length=255, null=True)
    runtime_config: fields.JSONField[dict[str, object] | None] = EncryptedJSONField(null=True)
    environment_vars: fields.JSONField[dict[str, str] | None] = EncryptedJSONField(null=True)

    class Meta:
        table = "project_files"
        indexes = [
            ("project_id",),
            ("language",),
            ("public_id",),
        ]


class ProjectRule(BaseModel):
    """规则项目详情"""

    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    project_id = fields.BigIntField(unique=True)

    engine = fields.CharEnumField(CrawlEngine, default=CrawlEngine.REQUESTS)
    target_url = fields.CharField(max_length=2000)
    url_pattern = fields.CharField(max_length=500, null=True)
    callback_type = fields.CharEnumField(CallbackType, default=CallbackType.LIST)
    request_method = fields.CharEnumField(RequestMethod, default=RequestMethod.GET)

    extraction_rules: fields.JSONField[object | None] = fields.JSONField(null=True)
    data_schema: fields.JSONField[object | None] = fields.JSONField(null=True)

    pagination_config: fields.JSONField[dict[str, object] | None] = fields.JSONField(null=True)
    max_pages = fields.IntField(default=10)
    start_page = fields.IntField(default=1)

    request_delay = fields.IntField(default=1000)
    retry_count = fields.IntField(default=3)
    timeout = fields.IntField(default=30)
    priority = fields.IntField(default=0)
    dont_filter = fields.BooleanField(default=False)

    headers: fields.JSONField[dict[str, str] | None] = EncryptedJSONField(null=True)
    cookies: fields.JSONField[dict[str, str] | None] = EncryptedJSONField(null=True)
    proxy_config: fields.JSONField[dict[str, object] | None] = EncryptedJSONField(null=True)
    anti_spider: fields.JSONField[dict[str, object] | None] = fields.JSONField(null=True)

    task_config: fields.JSONField[dict[str, object] | None] = EncryptedJSONField(null=True)

    # S3b: scrapy-redis 断点续爬 + 单爬虫分布式（跟 Scrapy 引擎配合）
    resume_enabled = fields.BooleanField(default=False)

    # S5: 内容级去重（跨 run 按业务字段哈希持久化）。JSON 结构:
    # {enabled: bool, fields: [str], scope: "project"|"run",
    #  ttl_days: int, on_hit: "drop"|"log"}
    dedup_config: fields.JSONField[dict[str, object] | None] = fields.JSONField(null=True)

    class Meta:
        table = "project_rules"
        indexes = [
            ("project_id",),
            ("engine",),
            ("callback_type",),
            ("public_id",),
        ]

    def to_dispatch_dict(self) -> dict[str, object]:
        """序列化为分发字典，供 dispatcher 使用。

        R1-P1-21 (审查报告)：DB 里配置了 ``request_delay/retry_count/timeout/
        max_pages/start_page`` 但 to_dispatch_dict 从不下发，Scrapy 侧
        （settings.py 读顶层 rule，spider 读 max_pages/start_page 兜底）
        永远拿不到，恒用默认 1s 延迟 / 3 重试 / 30s 超时 / 10 页 / 8 并发。
        **限速失效有对目标站过度请求的合规风险**——统一在这里补齐。
        """
        data: dict[str, object] = {
            "target_url": self.target_url,
            "callback_type": self.callback_type.value if hasattr(self.callback_type, "value") else self.callback_type,
            "request_method": self.request_method.value
            if hasattr(self.request_method, "value")
            else self.request_method,
            "engine": self.engine.value if hasattr(self.engine, "value") else self.engine,
            "headers": self.headers or {},
            "cookies": self.cookies or {},
            "priority": self.priority or 0,
            "dont_filter": self.dont_filter,
            # R1-P1-21: 限速/重试/超时/页数/并发 — Scrapy settings.py 读顶层
            "request_delay": int(self.request_delay or 1000),  # 毫秒；settings 会 /1000 转秒
            "retry_count": int(self.retry_count or 3),
            "timeout": int(self.timeout or 30),
            "max_pages": int(self.max_pages or 10),
            "start_page": int(self.start_page or 1),
        }
        if self.proxy_config:
            data["proxy_config"] = self.proxy_config
        if self.extraction_rules:
            data["extraction_rules"] = self.extraction_rules
        if self.pagination_config:
            data["pagination_config"] = self.pagination_config
        # R1-P2-27 (审查报告): 老实现 ``hasattr(self, "wait_time")`` /
        # ``hasattr(..., "javascript_code")`` / ``hasattr(..., "request_body")``
        # 三个字段是防御性代码，ProjectRule 模型**根本没有这三列**，
        # hasattr 恒 False，特性永远不可达。javascript_code (页面 JS 注入)
        # 用户实际想用这个功能只能通过 pagination_config.method=js_click。
        # 三个 hasattr 分支已清理；如果未来真要支持，需要先加 DB 列 +
        # 迁移。
        # 死列（url_pattern / anti_spider / data_schema / task_config /
        # callback_type）现在也不在这里下发。callback_type spider 不读；
        # 其他三个仅入库不消费——保留 DB 列但不参与 dispatch。
        # S3b: scrapy-redis 断点续爬开关（缺省 False，仅开启时下发）
        if getattr(self, "resume_enabled", False):
            data["resume_enabled"] = True
        # S5: 内容级去重配置（有配置时才下发）
        dedup_cfg = getattr(self, "dedup_config", None)
        if dedup_cfg:
            data["dedup_config"] = dedup_cfg
        return data


class ProjectCode(BaseModel):
    """代码项目详情"""

    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    project_id = fields.BigIntField(unique=True)

    language = fields.CharField(max_length=50, default="python")

    entry_point = fields.CharField(max_length=255, null=True)
    runtime_config: fields.JSONField[dict[str, object] | None] = EncryptedJSONField(null=True)
    environment_vars: fields.JSONField[dict[str, str] | None] = EncryptedJSONField(null=True)

    documentation = fields.TextField(null=True)
    changelog = fields.TextField(null=True)

    class Meta:
        table = "project_codes"
        indexes = [
            ("project_id",),
            ("language",),
            ("public_id",),
        ]


__all__ = [
    "Project",
    "ProjectFile",
    "ProjectRule",
    "ProjectCode",
]
