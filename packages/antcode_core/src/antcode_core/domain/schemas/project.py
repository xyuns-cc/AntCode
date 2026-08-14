"""
项目相关的Pydantic模式定义
包含项目创建、更新、响应等数据模式
"""

from datetime import datetime
from typing import Any, Literal

from antcode_contracts.runtime_metadata import RUNTIME_DESCRIPTION_MAX_LENGTH, RUNTIME_KEY_MAX_LENGTH
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from antcode_core.common.utils.json_parser import JSONParser
from antcode_core.domain.models.enums import (
    CallbackType,
    CrawlEngine,
    ProjectStatus,
    ProjectType,
    RequestMethod,
    RuntimeKind,
    RuntimeScope,
)

_MIN_PYTHON_VERSION_LENGTH = 3


def _normalize_runtime_scope(value):
    if isinstance(value, RuntimeScope):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw == "shared":
            return RuntimeScope.SHARED
        if raw == "private":
            return RuntimeScope.PRIVATE
    return value


def _normalize_runtime_kind(value):
    if isinstance(value, RuntimeKind):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw == "python":
            return RuntimeKind.PYTHON
        if raw == "java":
            return RuntimeKind.JAVA
        if raw == "go":
            return RuntimeKind.GO
    return value


def _parse_project_dict_field(value, field_name):
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return {}
        return JSONParser.parse_safely(value, field_name)
    return value


def _parse_project_list_field(value, field_name):
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        return JSONParser.parse_list(value, field_name) or []
    return value


class ExtractionRule(BaseModel):
    """提取规则模型"""

    desc: str = Field(..., description="规则描述")
    type: Literal["css", "xpath", "regex"] = Field(..., description="规则类型")
    expr: str = Field(..., description="规则表达式")
    page_type: str | None = Field(None, description="页面类型：list/detail，不指定则继承项目的callback_type")


class NextPageSelector(BaseModel):
    """下一页元素定位器。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["css", "xpath", "text"] = Field(..., description="选择器类型")
    expr: str = Field(..., min_length=1, description="选择器表达式")


class PaginationConfig(BaseModel):
    """分页配置模型"""

    model_config = ConfigDict(extra="forbid")

    method: Literal[
        "none",
        "url_pattern",
        "url_param",
        "click_element",
        "js_click",
        "infinite_scroll",
        "javascript",
        "ajax",
    ] = Field(..., description="分页方法")
    start_page: int = Field(1, ge=0, description="起始页码")
    max_pages: int = Field(10, ge=1, le=1000, description="最大页数")
    next_page_rule: str | NextPageSelector | None = Field(None, description="下一页规则（点击翻页用）")
    wait_after_click_ms: int | None = Field(None, ge=0, le=30000, description="点击后等待时间（毫秒）")
    url_template: str | None = Field(None, max_length=2000, description="分页 URL 模板")
    page_param: str | None = Field(None, min_length=1, max_length=100, description="页码查询参数名")
    scroll_count: int | None = Field(None, ge=1, le=100, description="滚动次数")
    scroll_wait_ms: int | None = Field(None, ge=0, le=10000, description="每次滚动等待时间")
    ajax_endpoint: str | None = Field(None, max_length=2000, description="旧版 AJAX 端点")
    ajax_params: dict[str, Any] | None = Field(None, description="旧版 AJAX 参数")


class ProjectCreateRequest(BaseModel):
    """项目创建请求基础模式"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=3, max_length=50, description="项目名称")
    description: str | None = Field(None, max_length=500, description="项目描述")
    type: str = Field(..., description="项目类型")
    tags: list[str] | None = Field(None, description="项目标签")
    dependencies: list[str] | None = Field(None, description="Python依赖包")

    env_location: str = Field(default="worker", description="环境位置")
    worker_id: str | None = Field(None, description="Worker 节点 ID")

    runtime_scope: RuntimeScope = Field(..., description="运行时作用域：shared/private")
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON, description="运行时类型：python/java/go")

    use_existing_env: bool = Field(default=False, description="是否使用现有环境")
    existing_env_name: str | None = Field(None, description="现有环境名称（节点环境）或标识（本地共享环境）")
    env_name: str | None = Field(None, max_length=100, description="新建运行环境名称")
    env_description: str | None = Field(None, max_length=RUNTIME_DESCRIPTION_MAX_LENGTH, description="运行环境描述")

    python_version: str | None = Field(None, min_length=3, max_length=20, description="Python版本")
    shared_runtime_key: str | None = Field(
        None,
        max_length=RUNTIME_KEY_MAX_LENGTH,
        description="共享运行时标识（可选），默认为版本目录",
    )

    region: str | None = Field(None, max_length=50, description="执行区域，系统自动选择该区域内负载最低的节点")

    execution_strategy: str | None = Field("auto", description="执行策略：fixed/auto/specified/prefer")
    bound_worker_id: str | None = Field(None, description="绑定的执行 Worker ID（用于 fixed/prefer 策略）")

    @field_validator("execution_strategy")
    @classmethod
    def validate_execution_strategy(cls, v):
        if v is not None and v.lower() == "local":
            raise ValueError("Invalid execution strategy")
        return v

    @field_validator("runtime_scope", mode="before")
    @classmethod
    def normalize_runtime_scope(cls, v):
        return _normalize_runtime_scope(v)

    @field_validator("runtime_kind", mode="before")
    @classmethod
    def normalize_runtime_kind(cls, v):
        return _normalize_runtime_kind(v)

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                result = JSONParser.parse_list(v, "tags")
                return result if result is not None else []
            else:
                return [tag.strip() for tag in v.split(",") if tag.strip()]
        return v

    @field_validator("dependencies", mode="before")
    @classmethod
    def parse_dependencies(cls, v):
        """解析依赖包字段"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_list(v, "dependencies")
        return v

    @model_validator(mode="after")
    def validate_runtime_binding(self):
        """规则项目按能力调度；文件/代码项目必须绑定有效 Worker 运行时。"""
        if self.type in {ProjectType.RULE, ProjectType.RULE.value}:
            return self
        if not self.worker_id:
            raise ValueError("必须指定 worker_id")
        if self.use_existing_env and not self.existing_env_name:
            raise ValueError("使用现有环境时必须指定 existing_env_name")
        if not self.use_existing_env and not self.python_version:
            raise ValueError("创建新环境时必须指定 python_version")
        if self.runtime_scope == RuntimeScope.PRIVATE and not self.use_existing_env:
            if not isinstance(self.python_version, str) or len(self.python_version) < _MIN_PYTHON_VERSION_LENGTH:
                raise ValueError("私有环境必须提供有效的 Python 版本")
        return self


class ProjectFileCreateRequest(ProjectCreateRequest):
    """文件项目创建请求"""

    language: str | None = Field(
        "python", max_length=50, description="执行语言（python/node/go/java 等，默认 python 兼容旧项目）"
    )
    entry_point: str | None = Field(None, max_length=255, description="入口文件路径")
    runtime_config: dict | None = Field(None, description="运行时配置")
    environment_vars: dict | None = Field(None, description="环境变量")
    repository_id: str = Field(..., max_length=32, description="Git 仓库 ID")
    ref: str = Field("main", max_length=255, description="Git 引用")
    subdir: str = Field(..., max_length=500, description="项目子目录")
    include_paths: list[str] = Field(default_factory=list, description="显式共享路径")

    @field_validator("runtime_config", mode="before")
    @classmethod
    def parse_runtime_config(cls, v):
        """解析运行时配置"""
        return _parse_project_dict_field(v, "runtime_config")

    @field_validator("environment_vars", mode="before")
    @classmethod
    def parse_environment_vars(cls, v):
        """解析环境变量"""
        return _parse_project_dict_field(v, "environment_vars")

    @field_validator("include_paths", mode="before")
    @classmethod
    def parse_include_paths(cls, v):
        return _parse_project_list_field(v, "include_paths")


class ProjectRuleCreateRequest(ProjectCreateRequest):
    """规则项目创建请求"""

    runtime_scope: RuntimeScope = Field(RuntimeScope.SHARED, description="规则项目不绑定 Worker 运行时")
    engine: CrawlEngine = Field(CrawlEngine.REQUESTS, description="采集引擎")
    require_render: bool = Field(False, description="是否要求 Worker 具备浏览器渲染能力")
    target_url: str = Field(..., max_length=2000, description="目标URL")
    url_pattern: str | None = Field(None, max_length=500, description="URL匹配模式")
    request_method: RequestMethod = Field(RequestMethod.GET, description="请求方法")
    callback_type: CallbackType = Field(CallbackType.LIST, description="回调类型")
    extraction_rules: list[ExtractionRule] | None = Field(None, description="提取规则数组")
    pagination_config: PaginationConfig | None = Field(None, description="分页配置")
    max_pages: int = Field(10, ge=1, le=1000, description="最大页数")
    start_page: int = Field(1, ge=0, description="起始页码")
    request_delay: int = Field(1000, ge=0, description="请求间隔(ms)")
    retry_count: int = Field(3, ge=0, le=10, description="重试次数")
    timeout: int = Field(30, ge=1, le=300, description="超时时间(s)")
    priority: int = Field(0, description="优先级")
    dont_filter: bool = Field(False, description="是否去重")
    data_schema: dict[str, Any] | None = Field(None, description="数据结构定义")
    headers: dict | None = Field(None, description="请求头")
    cookies: dict | None = Field(None, description="Cookie")
    proxy_config: dict[str, Any] | None = Field(None, description="代理配置")
    anti_spider: dict[str, Any] | None = Field(None, description="反爬虫配置")
    task_config: dict[str, Any] | None = Field(None, description="任务配置")
    resume_enabled: bool | None = Field(None, description="scrapy-redis 断点续爬 + 分布式")
    dedup_config: dict[str, Any] | None = Field(None, description="内容级去重（fields/scope/ttl_days/on_hit）")

    @field_validator("extraction_rules", mode="before")
    @classmethod
    def parse_extraction_rules(cls, v):
        """解析提取规则JSON字符串 - 支持单引号和双引号"""
        if v is None:
            return None
        if isinstance(v, str):
            parsed = JSONParser.parse_extraction_rules(v, "extraction_rules")
            if parsed is None:
                raise ValueError("extraction_rules JSON格式错误")

            try:
                return [ExtractionRule(**rule) if isinstance(rule, dict) else rule for rule in parsed]
            except Exception as e:
                raise ValueError(f"extraction_rules 对象转换错误: {str(e)}")
        return v

    @field_validator("pagination_config", mode="before")
    @classmethod
    def parse_pagination_config(cls, v):
        """解析分页配置JSON字符串 - 支持单引号和双引号"""
        if v is None:
            return None
        if isinstance(v, str):
            parsed = JSONParser.parse_pagination_config(v, "pagination_config")
            if parsed is None:
                raise ValueError("pagination_config JSON格式错误")

            try:
                return PaginationConfig(**parsed) if isinstance(parsed, dict) else parsed
            except Exception as e:
                raise ValueError(f"pagination_config 对象转换错误: {str(e)}")
        return v

    @field_validator(
        "data_schema",
        "headers",
        "cookies",
        "proxy_config",
        "anti_spider",
        "task_config",
        "dedup_config",
        mode="before",
    )
    @classmethod
    def parse_json_fields(cls, v, info):
        """解析JSON字段 - 支持单引号和双引号"""
        return _parse_project_dict_field(v, info.field_name)

    @field_validator("extraction_rules")
    @classmethod
    def validate_extraction_rules(cls, v):
        """验证提取规则"""
        if not v:
            raise ValueError("必须提供提取规则")
        return v


class ProjectCodeCreateRequest(ProjectCreateRequest):
    """代码项目创建请求"""

    language: str | None = Field("python", max_length=50, description="编程语言")
    entry_point: str | None = Field(None, max_length=255, description="入口文件")
    documentation: str | None = Field(None, description="代码文档")
    repository_id: str = Field(..., max_length=32, description="Git 仓库 ID")
    ref: str = Field("main", max_length=255, description="Git 引用")
    subdir: str = Field(..., max_length=500, description="项目子目录")
    include_paths: list[str] = Field(default_factory=list, description="显式共享路径")

    @field_validator("include_paths", mode="before")
    @classmethod
    def parse_include_paths(cls, v):
        return _parse_project_list_field(v, "include_paths")


class ProjectUpdateRequest(BaseModel):
    """项目更新请求"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=3, max_length=50, description="项目名称")
    description: str | None = Field(None, max_length=500, description="项目描述")
    status: ProjectStatus | None = Field(None, description="项目状态")
    tags: list[str] | None = Field(None, description="项目标签")
    dependencies: list[str] | None = Field(None, description="Python依赖包")

    region: str | None = Field(None, max_length=50, description="执行区域")

    execution_strategy: str | None = Field(None, description="执行策略：fixed/auto/specified/prefer")
    bound_worker_id: str | None = Field(None, description="绑定的执行 Worker ID")

    @field_validator("execution_strategy")
    @classmethod
    def validate_execution_strategy(cls, v):
        """验证执行策略"""
        if v is not None and v.lower() == "local":
            raise ValueError("无效的执行策略")
        return v


# Form参数模型（用于multipart/form-data请求）
class ProjectCreateFormRequest(BaseModel):
    """项目创建 Form 请求模式。项目源码必须来自 Git 仓库。"""

    model_config = ConfigDict(extra="forbid")

    # 通用参数
    name: str = Field(..., min_length=3, max_length=50, description="项目名称")
    description: str | None = Field(None, max_length=500, description="项目描述")
    type: ProjectType = Field(..., description="项目类型")
    tags: str | None = Field(None, description="项目标签，逗号分隔或JSON数组")
    dependencies: str | None = Field(None, description="Python依赖包JSON数组")
    runtime_scope: RuntimeScope = Field(..., description="运行时作用域：shared/private")
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON, description="运行时类型：python/java/go")
    python_version: str | None = Field(None, max_length=20, description="Python版本（私有环境必填）")
    shared_runtime_key: str | None = Field(
        None, max_length=RUNTIME_KEY_MAX_LENGTH, description="共享运行时标识（可选）"
    )

    # Worker 环境参数
    env_location: str | None = Field("worker", description="环境位置：worker（Worker 执行）")
    worker_id: str | None = Field(None, description="Worker ID")
    use_existing_env: bool | None = Field(False, description="是否使用现有环境")
    existing_env_name: str | None = Field(None, description="现有环境名称")
    env_name: str | None = Field(None, description="新环境名称")
    env_description: str | None = Field(None, max_length=RUNTIME_DESCRIPTION_MAX_LENGTH, description="环境描述")

    # 文件项目参数
    entry_point: str | None = Field(None, max_length=255, description="入口文件路径")
    runtime_config: str | None = Field(None, description="运行时配置JSON")
    environment_vars: str | None = Field(None, description="环境变量JSON")

    # 区域配置
    region: str | None = Field(None, max_length=50, description="执行区域")
    require_render: bool = Field(False, description="是否要求 Worker 具备浏览器渲染能力")

    # 规则项目参数
    engine: CrawlEngine | None = Field(CrawlEngine.REQUESTS, description="采集引擎")
    target_url: str | None = Field(None, max_length=2000, description="目标URL")
    url_pattern: str | None = Field(None, max_length=500, description="URL匹配模式")
    request_method: str | None = Field("GET", description="请求方法 (GET/POST/PUT/DELETE)")
    callback_type: str | None = Field("list", description="回调类型 (list/detail)")
    extraction_rules: str | None = Field(None, description="提取规则数组JSON")
    pagination_config: str | None = Field(None, description="分页配置JSON")
    max_pages: int | None = Field(10, ge=1, le=1000, description="最大页数")
    start_page: int | None = Field(1, ge=0, description="起始页码")
    request_delay: int | None = Field(1000, ge=0, description="请求间隔(ms)")
    retry_count: int | None = Field(3, ge=0, le=10, description="重试次数")
    timeout: int | None = Field(30, ge=1, le=300, description="超时时间(s)")
    priority: int | None = Field(0, description="优先级")
    dont_filter: bool | None = Field(False, description="是否去重")
    data_schema: str | None = Field(None, description="数据结构定义JSON")
    headers: str | None = Field(None, description="请求头JSON")
    cookies: str | None = Field(None, description="Cookie JSON")
    proxy_config: str | None = Field(None, description="代理配置JSON")
    anti_spider: str | None = Field(None, description="反爬虫配置JSON")
    task_config: str | None = Field(None, description="任务配置JSON")
    # S10 (Scrapy 迁移收尾)
    resume_enabled: bool | None = Field(None, description="scrapy-redis 断点续爬 + 分布式")
    dedup_config: str | None = Field(None, description="内容级去重配置JSON")

    # 代码项目参数
    language: str | None = Field("python", max_length=50, description="编程语言")
    code_entry_point: str | None = Field(None, max_length=255, description="入口文件")
    documentation: str | None = Field(None, description="代码文档")
    repository_id: str | None = Field(None, max_length=32, description="Git 仓库 ID")
    ref: str | None = Field(None, max_length=255, description="Git 引用")
    subdir: str | None = Field(None, max_length=500, description="项目子目录")
    include_paths: list[str] = Field(default_factory=list, description="显式共享路径")

    @field_validator("include_paths", mode="before")
    @classmethod
    def parse_include_paths(cls, v):
        return _parse_project_list_field(v, "include_paths")

    @field_validator("runtime_scope", mode="before")
    @classmethod
    def normalize_runtime_scope(cls, v):
        return _normalize_runtime_scope(v)

    @field_validator("runtime_kind", mode="before")
    @classmethod
    def normalize_runtime_kind(cls, v):
        return _normalize_runtime_kind(v)


class ProjectListQueryRequest(BaseModel):
    """项目列表查询参数模式"""

    page: int = Field(1, ge=1, description="页码")
    size: int = Field(20, ge=1, le=500, description="每页数量")
    type: ProjectType | None = Field(None, description="项目类型筛选")
    status: ProjectStatus | None = Field(None, description="项目状态筛选")
    tag: str | None = Field(None, description="标签筛选")
    created_by: str | None = Field(None, description="创建者公开ID筛选")
    search: str | None = Field(None, description="关键字搜索（名称、描述）")
    worker_id: str | None = Field(None, description="Worker ID 筛选")


class TaskMeta(BaseModel):
    """任务元数据模型"""

    fetch_type: CrawlEngine = Field(..., description="爬取方式")
    pagination: PaginationConfig | None = Field(None, description="分页配置")
    rules: list[ExtractionRule] = Field(..., description="提取规则列表")
    page_number: int | None = Field(None, description="当前页码")
    proxy: str | None = Field(None, description="代理配置")
    task_id: str | None = Field(None, description="任务ID")
    worker_id: str | None = Field(None, description="Worker ID")


class TaskJsonRequest(BaseModel):
    """任务JSON请求模型"""

    url: str = Field(..., description="目标URL")
    callback: CallbackType = Field(..., description="回调类型")
    method: RequestMethod = Field(RequestMethod.GET, description="请求方法")
    meta: TaskMeta = Field(..., description="任务元数据")
    headers: dict[str, str] | None = Field(None, description="请求头")
    cookies: dict[str, str] | None = Field(None, description="Cookie")
    priority: int = Field(0, description="优先级")
    dont_filter: bool = Field(False, description="是否去重")


class ProjectRuleUpdateRequest(BaseModel):
    """规则项目更新请求模型"""

    model_config = ConfigDict(extra="forbid")

    engine: CrawlEngine | None = Field(None, description="采集引擎")
    region: str | None = Field(None, max_length=50, description="执行区域")
    require_render: bool | None = Field(None, description="是否要求 Worker 具备浏览器渲染能力")
    target_url: str | None = Field(None, max_length=2000, description="目标URL")
    url_pattern: str | None = Field(None, max_length=500, description="URL匹配模式")
    callback_type: CallbackType | None = Field(None, description="回调类型")
    request_method: RequestMethod | None = Field(None, description="请求方法")
    extraction_rules: list[ExtractionRule] | None = Field(None, description="提取规则数组")
    pagination_config: PaginationConfig | None = Field(None, description="分页配置")
    max_pages: int | None = Field(None, ge=1, le=1000, description="最大页数")
    start_page: int | None = Field(None, ge=0, description="起始页码")
    request_delay: int | None = Field(None, ge=0, description="请求间隔(ms)")
    retry_count: int | None = Field(None, ge=0, le=10, description="重试次数")
    timeout: int | None = Field(None, ge=1, le=300, description="超时时间(s)")
    priority: int | None = Field(None, description="优先级")
    dont_filter: bool | None = Field(None, description="是否去重")
    data_schema: dict[str, Any] | None = Field(None, description="数据结构定义")
    headers: dict[str, str] | None = Field(None, description="请求头")
    cookies: dict[str, str] | None = Field(None, description="Cookie")
    proxy_config: dict[str, Any] | None = Field(None, description="代理配置")
    anti_spider: dict[str, Any] | None = Field(None, description="反爬虫配置")
    task_config: dict[str, Any] | None = Field(None, description="任务配置")
    resume_enabled: bool | None = Field(None, description="scrapy-redis 断点续爬 + 分布式")
    dedup_config: dict[str, Any] | None = Field(None, description="内容级去重（fields/scope/ttl_days/on_hit）")

    @field_validator(
        "data_schema",
        "headers",
        "cookies",
        "proxy_config",
        "anti_spider",
        "task_config",
        "dedup_config",
        mode="before",
    )
    @classmethod
    def parse_json_update_fields(cls, v, info):
        return _parse_project_dict_field(v, info.field_name)


class ProjectCodeUpdateRequest(BaseModel):
    """代码项目更新请求模型"""

    model_config = ConfigDict(extra="forbid")

    language: str | None = Field(None, max_length=50, description="编程语言")
    entry_point: str | None = Field(None, max_length=255, description="入口文件")
    documentation: str | None = Field(None, description="代码文档")
    runtime_config: dict[str, Any] | None = Field(None, description="运行时配置")
    environment_vars: dict[str, Any] | None = Field(None, description="环境变量")


class ProjectFileUpdateRequest(BaseModel):
    """文件项目更新请求模型"""

    model_config = ConfigDict(extra="forbid")

    language: str | None = Field(None, max_length=50, description="执行语言")
    entry_point: str | None = Field(None, max_length=255, description="入口文件路径")
    runtime_config: str | dict[str, Any] | None = Field(None, description="运行时配置")
    environment_vars: str | dict[str, Any] | None = Field(None, description="环境变量")

    @field_validator("runtime_config", mode="before")
    @classmethod
    def parse_runtime_config(cls, v):
        """解析运行时配置"""
        return _parse_project_dict_field(v, "runtime_config")

    @field_validator("environment_vars", mode="before")
    @classmethod
    def parse_environment_vars(cls, v):
        """解析环境变量"""
        return _parse_project_dict_field(v, "environment_vars")


class FileInfo(BaseModel):
    """Git 文件项目信息"""

    language: str = Field("python", description="执行语言")
    entry_point: str = Field("", description="入口文件")
    runtime_config: dict[str, Any] = Field(default_factory=dict, description="运行时配置")
    environment_vars: dict[str, Any] = Field(default_factory=dict, description="环境变量")
    repository_id: str | None = Field(None, description="Git 仓库 ID")
    repository_name: str | None = Field(None, description="Git 仓库名称")
    repository_url: str | None = Field(None, description="Git 仓库地址")
    ref: str | None = Field(None, description="Git 引用")
    subdir: str | None = Field(None, description="项目子目录")
    include_paths: list[str] = Field(default_factory=list, description="显式共享路径")
    resolved_revision: str | None = Field(None, description="Git 解析后的提交版本")


class CodeInfo(FileInfo):
    """Git 代码项目信息。"""

    language: str = Field("python", description="编程语言")
    documentation: str | None = Field(None, description="代码文档")


class ProjectResponse(BaseModel):
    """项目响应模式"""

    id: str = Field(description="项目公开ID")
    name: str = Field(description="项目名称")
    description: str = Field("", description="项目描述")
    type: ProjectType = Field(description="项目类型")
    status: ProjectStatus = Field(description="项目状态")
    tags: list[str] = Field(default_factory=list, description="项目标签")
    dependencies: list[str] | None = Field(default_factory=list, description="Python依赖包")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    created_by: str = Field("", description="创建者公开ID")
    created_by_username: str | None = Field(None, description="创建者用户名")
    star_count: int = Field(0, description="收藏次数")
    # 运行环境
    env_location: str | None = Field(None, description="运行环境位置")
    worker_id: str | None = Field(None, description="运行环境 Worker ID")
    worker_env_name: str | None = Field(None, description="Worker 环境名称")
    python_version: str | None = Field(None, description="绑定的Python版本")
    runtime_scope: RuntimeScope | None = Field(None, description="运行时作用域")
    runtime_kind: RuntimeKind | None = Field(None, description="运行时类型")
    runtime_locator: str | None = Field(None, description="运行时定位符")
    file_info: FileInfo | None = Field(None, description="文件信息")
    rule_info: dict[str, Any] | None = Field(default_factory=dict, description="规则信息")
    code_info: CodeInfo | None = Field(None, description="代码信息")

    # 执行策略配置
    execution_strategy: str = Field("", description="执行策略")
    bound_worker_id: str | None = Field(None, description="绑定的执行 Worker ID")
    bound_worker_name: str | None = Field(None, description="绑定的执行 Worker 名称")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ProjectListResponse(BaseModel):
    """项目列表响应模式"""

    id: str = Field(description="项目公开ID")
    name: str = Field(description="项目名称")
    description: str = Field("", description="项目描述")
    type: ProjectType = Field(description="项目类型")
    status: ProjectStatus = Field(description="项目状态")
    tags: list[str] = Field(default_factory=list, description="项目标签")
    created_at: datetime = Field(description="创建时间")
    created_by: str = Field("", description="创建者公开ID")
    created_by_username: str = Field("", description="创建者用户名")
    star_count: int = Field(0, description="收藏次数")
    task_count: int = Field(0, description="任务数量")

    model_config = ConfigDict(from_attributes=True)
