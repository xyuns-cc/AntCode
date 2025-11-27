"""项目模型"""

from tortoise import fields
from tortoise.models import Model

from src.models.enums import ProjectType, ProjectStatus, CrawlEngine, CallbackType, RequestMethod, VenvScope


class Project(Model):
    """项目模型"""
    id = fields.BigIntField(pk=True)
    name = fields.CharField(max_length=255, unique=True)
    description = fields.TextField(null=True)
    type = fields.CharEnumField(ProjectType)
    status = fields.CharEnumField(ProjectStatus, default=ProjectStatus.DRAFT)

    tags = fields.JSONField(default=list)
    dependencies = fields.JSONField(null=True)

    python_version = fields.CharField(max_length=20, null=True)
    venv_scope = fields.CharEnumField(VenvScope, null=True)
    venv_path = fields.CharField(max_length=500, null=True)
    current_venv_id = fields.BigIntField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    updated_by = fields.BigIntField(null=True)
    user_id = fields.BigIntField()

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
            ("python_version",),
            ("venv_scope",),
            ("created_at",),
            ("updated_at",),
            ("current_venv_id",),
            ("type", "status"),
            ("user_id", "status"),
            ("status", "created_at"),
            ("status", "updated_at"),
        ]


class ProjectFile(Model):
    """文件项目详情"""
    id = fields.BigIntField(pk=True)
    project_id = fields.BigIntField(unique=True)

    file_path = fields.CharField(max_length=500)
    original_file_path = fields.CharField(max_length=500, null=True)
    original_name = fields.CharField(max_length=255)
    file_size = fields.BigIntField()
    file_type = fields.CharField(max_length=50)
    file_hash = fields.CharField(max_length=64)

    entry_point = fields.CharField(max_length=255, null=True)
    runtime_config = fields.JSONField(null=True)
    environment_vars = fields.JSONField(null=True)

    storage_type = fields.CharField(max_length=20, default="local")
    is_compressed = fields.BooleanField(default=False)
    compression_ratio = fields.FloatField(null=True)
    
    file_count = fields.IntField(default=1)
    additional_files = fields.JSONField(null=True)

    class Meta:
        table = "project_files"
        indexes = [
            ("project_id",),
            ("file_type",),
            ("storage_type",),
            ("is_compressed",),
        ]


class ProjectRule(Model):
    """规则项目详情"""
    id = fields.BigIntField(pk=True)
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
        ]


class ProjectCode(Model):
    """代码项目详情"""
    id = fields.BigIntField(pk=True)
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
        ]
