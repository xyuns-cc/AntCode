"""
项目相关数据模型
包含项目主表和各种项目类型的详情表
"""

from tortoise import fields
from tortoise.models import Model

from .enums import ProjectType, ProjectStatus, CrawlEngine, CallbackType, RequestMethod, VenvScope


class Project(Model):
    """项目主表"""
    id = fields.BigIntField(pk=True)
    name = fields.CharField(max_length=255, unique=True, description="项目名称")
    description = fields.TextField(null=True, description="项目描述")
    type = fields.CharEnumField(ProjectType, description="项目类型")
    status = fields.CharEnumField(ProjectStatus, default=ProjectStatus.DRAFT, description="项目状态")

    # 标签和依赖
    tags = fields.JSONField(default=list, description="项目标签")
    dependencies = fields.JSONField(null=True, description="Python依赖包")

    # 运行环境绑定（Python）
    python_version = fields.CharField(max_length=20, null=True, description="绑定的Python版本")
    venv_scope = fields.CharEnumField(VenvScope, null=True, description="虚拟环境作用域：shared/private")
    venv_path = fields.CharField(max_length=500, null=True, description="虚拟环境路径")
    current_venv_id = fields.BigIntField(null=True, description="当前绑定的虚拟环境ID（应用层外键）")

    # 时间和用户信息
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    updated_at = fields.DatetimeField(auto_now=True, description="更新时间")
    updated_by = fields.BigIntField(null=True, description="更新者ID")

    # 关联关系 - 使用应用层外键
    user_id = fields.BigIntField(description="创建者ID")

    # 统计信息
    download_count = fields.IntField(default=0, description="下载次数")
    star_count = fields.IntField(default=0, description="收藏次数")

    def __str__(self):
        return self.name

    class Meta:
        table = "projects"
        indexes = [("name",), ("type",), ("status",), ("user_id",), ("python_version",), ("venv_scope",)]


class ProjectFile(Model):
    """文件项目详情"""
    id = fields.BigIntField(pk=True)
    project_id = fields.BigIntField(unique=True, description="关联项目ID")

    # 文件信息
    file_path = fields.CharField(max_length=500, description="存储路径（解压后路径或单文件路径）")
    original_file_path = fields.CharField(max_length=500, null=True, description="原始文件路径（压缩包路径）")
    original_name = fields.CharField(max_length=255, description="原始文件名")
    file_size = fields.BigIntField(description="文件大小(字节)")
    file_type = fields.CharField(max_length=50, description="文件类型")
    file_hash = fields.CharField(max_length=64, description="MD5哈希")

    # 执行配置
    entry_point = fields.CharField(max_length=255, null=True, description="入口文件")
    runtime_config = fields.JSONField(null=True, description="运行时配置")
    environment_vars = fields.JSONField(null=True, description="环境变量")

    # 存储配置
    storage_type = fields.CharField(max_length=20, default="local", description="存储类型")
    is_compressed = fields.BooleanField(default=False, description="是否压缩")
    compression_ratio = fields.FloatField(null=True, description="压缩比")
    
    # 多文件支持
    file_count = fields.IntField(default=1, description="文件数量")
    additional_files = fields.JSONField(null=True, description="附加文件信息（多文件上传时使用）")

    class Meta:
        table = "project_files"


class ProjectRule(Model):
    """规则项目详情"""
    id = fields.BigIntField(pk=True)
    project_id = fields.BigIntField(unique=True, description="关联项目ID")

    # 基础配置
    engine = fields.CharEnumField(CrawlEngine, default=CrawlEngine.REQUESTS, description="采集引擎")
    target_url = fields.CharField(max_length=2000, description="目标URL")
    url_pattern = fields.CharField(max_length=500, null=True, description="URL匹配模式")
    callback_type = fields.CharEnumField(CallbackType, default=CallbackType.LIST, description="回调类型")
    request_method = fields.CharEnumField(RequestMethod, default=RequestMethod.GET, description="请求方法")

    # 抓取规则 - 使用JSON格式存储规则数组
    extraction_rules = fields.JSONField(null=True, description="提取规则数组")
    data_schema = fields.JSONField(null=True, description="数据结构定义")

    # 翻页配置
    pagination_config = fields.JSONField(null=True, description="分页配置JSON")
    max_pages = fields.IntField(default=10, description="最大页数")
    start_page = fields.IntField(default=1, description="起始页码")

    # 请求配置
    request_delay = fields.IntField(default=1000, description="请求间隔(ms)")
    retry_count = fields.IntField(default=3, description="重试次数")
    timeout = fields.IntField(default=30, description="超时时间(s)")
    priority = fields.IntField(default=0, description="优先级")
    dont_filter = fields.BooleanField(default=False, description="是否去重")

    # 高级配置
    headers = fields.JSONField(null=True, description="请求头")
    cookies = fields.JSONField(null=True, description="Cookie")
    proxy_config = fields.JSONField(null=True, description="代理配置")
    anti_spider = fields.JSONField(null=True, description="反爬虫配置")

    # 任务配置
    task_config = fields.JSONField(null=True, description="任务配置（包含task_id模板、worker_id等）")

    class Meta:
        table = "project_rules"


class ProjectCode(Model):
    """代码项目详情"""
    id = fields.BigIntField(pk=True)
    project_id = fields.BigIntField(unique=True, description="关联项目ID")

    # 代码信息
    content = fields.TextField(description="代码内容")
    language = fields.CharField(max_length=50, default="python", description="编程语言")
    version = fields.CharField(max_length=20, default="1.0.0", description="版本号")
    content_hash = fields.CharField(max_length=64, description="内容哈希")

    # 执行配置
    entry_point = fields.CharField(max_length=255, null=True, description="入口函数")
    runtime_config = fields.JSONField(null=True, description="运行时配置")
    environment_vars = fields.JSONField(null=True, description="环境变量")

    # 文档信息
    documentation = fields.TextField(null=True, description="代码文档")
    changelog = fields.TextField(null=True, description="变更日志")

    class Meta:
        table = "project_codes"
