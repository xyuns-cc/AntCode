"""
项目模型

项目及其详情的数据模型定义。
"""

from tortoise import fields

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

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    name = fields.CharField(max_length=255, unique=True)
    description = fields.TextField(null=True)
    type = fields.CharEnumField(ProjectType)
    status = fields.CharEnumField(ProjectStatus, default=ProjectStatus.ACTIVE)

    tags = fields.JSONField(default=list)
    dependencies = fields.JSONField(null=True)

    # ========== 环境相关字段 ==========
    env_location = fields.CharField(
        max_length=10, null=True, default="worker", description="环境位置，仅支持 worker"
    )
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
    bound_worker_id = fields.BigIntField(
        null=True, db_index=True, description="绑定 Worker ID"
    )
    fallback_enabled = fields.BooleanField(default=True, description="启用故障转移")

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    updated_by = fields.BigIntField(null=True)
    user_id = fields.BigIntField()

    # 统计
    download_count = fields.IntField(default=0)
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
    """文件项目详情

    支持版本化存储：
    - 草稿（draft）：可编辑的当前工作区
    - 版本（version）：不可变的已发布快照
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    project_id = fields.BigIntField(unique=True)

    # ========== 文件基础信息 ==========
    file_path = fields.CharField(max_length=500, description="当前文件路径")
    original_file_path = fields.CharField(max_length=500, null=True, description="原始上传路径")
    original_name = fields.CharField(max_length=255)
    file_size = fields.BigIntField()
    file_type = fields.CharField(max_length=50)
    file_hash = fields.CharField(max_length=64)

    entry_point = fields.CharField(max_length=255, null=True)
    runtime_config = fields.JSONField(null=True)
    environment_vars = fields.JSONField(null=True)

    storage_type = fields.CharField(max_length=20, default="s3")
    is_compressed = fields.BooleanField(default=False)
    compression_ratio = fields.FloatField(null=True)

    file_count = fields.IntField(default=1)
    additional_files = fields.JSONField(null=True)

    # ========== 草稿管理（新增） ==========
    draft_manifest_key = fields.CharField(
        max_length=512, null=True, description="草稿 manifest S3 路径"
    )
    draft_root_prefix = fields.CharField(
        max_length=512, null=True, description="草稿文件树 S3 前缀"
    )

    # ========== 编辑状态（新增） ==========
    dirty = fields.BooleanField(default=False, description="草稿是否有未发布修改")
    dirty_files_count = fields.IntField(default=0, description="修改文件数")
    last_editor_id = fields.BigIntField(null=True, description="最后编辑者 ID")
    last_edit_at = fields.DatetimeField(null=True, description="最后编辑时间")

    # ========== 版本指针（新增） ==========
    published_version = fields.IntField(default=0, description="最新已发布版本号")

    class Meta:
        table = "project_files"
        indexes = [
            ("project_id",),
            ("file_type",),
            ("storage_type",),
            ("is_compressed",),
            ("public_id",),
            ("dirty",),
            ("published_version",),
        ]


class ProjectFileVersion(BaseModel):
    """项目文件版本（不可变 append-only）

    每次发布草稿时创建新版本，版本一旦创建不可修改。
    """

    project_id = fields.BigIntField(db_index=True, description="关联项目 ID")
    version = fields.IntField(description="版本序号 v1/v2...")
    version_id = fields.CharField(
        max_length=64, unique=True, description="不可变版本 ID（UUID）"
    )

    # S3 存储路径
    manifest_key = fields.CharField(max_length=512, description="S3 manifest 路径")
    artifact_key = fields.CharField(max_length=512, description="S3 artifact.zip 路径")

    # 内容摘要
    content_hash = fields.CharField(max_length=64, description="内容哈希（用于缓存/去重）")
    file_count = fields.IntField(default=0, description="文件数量")
    total_size = fields.BigIntField(default=0, description="总大小（字节）")

    # 元数据
    created_at = fields.DatetimeField(auto_now_add=True)
    created_by = fields.BigIntField(null=True, description="创建者 ID")
    description = fields.CharField(max_length=500, null=True, description="版本说明")

    class Meta:
        table = "project_file_versions"
        unique_together = [("project_id", "version")]
        indexes = [
            ("project_id",),
            ("project_id", "version"),
            ("version_id",),
            ("content_hash",),
            ("created_at",),
        ]


class ProjectRule(BaseModel):
    """规则项目详情"""

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    project_id = fields.BigIntField(unique=True)

    engine = fields.CharEnumField(CrawlEngine, default=CrawlEngine.REQUESTS)
    target_url = fields.CharField(max_length=2000)
    url_pattern = fields.CharField(max_length=500, null=True)
    callback_type = fields.CharEnumField(CallbackType, default=CallbackType.LIST)
    request_method = fields.CharEnumField(RequestMethod, default=RequestMethod.GET)

    extraction_rules = fields.JSONField(null=True)
    data_schema = fields.JSONField(null=True)

    pagination_config = fields.JSONField(null=True)
    max_pages = fields.IntField(default=10)
    start_page = fields.IntField(default=1)

    request_delay = fields.IntField(default=1000)
    retry_count = fields.IntField(default=3)
    timeout = fields.IntField(default=30)
    priority = fields.IntField(default=0)
    dont_filter = fields.BooleanField(default=False)

    headers = fields.JSONField(null=True)
    cookies = fields.JSONField(null=True)
    proxy_config = fields.JSONField(null=True)
    anti_spider = fields.JSONField(null=True)

    task_config = fields.JSONField(null=True)

    class Meta:
        table = "project_rules"
        indexes = [
            ("project_id",),
            ("engine",),
            ("callback_type",),
            ("public_id",),
        ]


class ProjectCode(BaseModel):
    """代码项目详情"""

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    project_id = fields.BigIntField(unique=True)

    content = fields.TextField()
    language = fields.CharField(max_length=50, default="python")
    version = fields.CharField(max_length=20, default="1.0.0")
    content_hash = fields.CharField(max_length=64)

    entry_point = fields.CharField(max_length=255, null=True)
    runtime_config = fields.JSONField(null=True)
    environment_vars = fields.JSONField(null=True)

    documentation = fields.TextField(null=True)
    changelog = fields.TextField(null=True)

    class Meta:
        table = "project_codes"
        indexes = [
            ("project_id",),
            ("language",),
            ("version",),
            ("content_hash",),
            ("public_id",),
        ]


__all__ = [
    "Project",
    "ProjectFile",
    "ProjectFileVersion",
    "ProjectRule",
    "ProjectCode",
]
