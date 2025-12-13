"""
项目相关的Pydantic模式定义
包含项目创建、更新、响应等数据模式
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.enums import ProjectType, ProjectStatus, CrawlEngine, CallbackType, RequestMethod, VenvScope
from src.utils.json_parser import JSONParser


class ExtractionRule(BaseModel):
    """提取规则模型"""
    desc: str = Field(..., description="规则描述")
    type: str = Field(..., description="规则类型")
    expr: str = Field(..., description="规则表达式")
    page_type: str | None = Field(None, description="页面类型：list/detail，不指定则继承项目的callback_type")


class PaginationConfig(BaseModel):
    """分页配置模型"""
    method: str = Field(..., description="分页方法")
    start_page: int = Field(1, description="起始页码")
    max_pages: int = Field(10, description="最大页数")
    next_page_rule: str | None = Field(None, description="下一页规则（点击翻页用）")
    wait_after_click_ms: int | None = Field(None, description="点击后等待时间（毫秒）")


class ProjectCreateRequest(BaseModel):
    """项目创建请求基础模式"""
    name: str = Field(..., min_length=3, max_length=50, description="项目名称")
    description: str | None = Field(None, max_length=500, description="项目描述")
    type: str = Field(..., description="项目类型")
    tags: list[str] | None = Field(None, description="项目标签")
    dependencies: list[str] | None = Field(None, description="Python依赖包")

    # ========== 环境配置（支持本地和节点）==========
    # 环境位置：local / node
    env_location: str = Field(default="local", description="环境位置：local（本地）/ node（节点）")

    # 节点ID（当 env_location='node' 时必填）
    node_id: str | None = Field(None, description="节点ID")

    # 环境作用域：private / shared
    venv_scope: VenvScope = Field(..., description="虚拟环境作用域：shared/private")

    # 使用现有环境 or 创建新环境
    use_existing_env: bool = Field(default=False, description="是否使用现有环境")
    existing_env_name: str | None = Field(None, description="现有环境名称（节点环境）或标识（本地共享环境）")

    # 创建新环境时的配置
    python_version: Optional[str] = Field(None, min_length=3, max_length=20, description="Python版本")
    shared_venv_key: str | None = Field(None, description="共享环境标识（可选），默认为版本目录")
    interpreter_source: str | None = Field("mise", description="解释器来源：mise/local")
    python_bin: str | None = Field(None, description="当来源为local时的python路径")

    # ========== 区域配置 ==========
    region: str | None = Field(None, description="执行区域，系统自动选择该区域内负载最低的节点")

    # ========== 执行策略配置（保留兼容）==========
    execution_strategy: str | None = Field(
        "auto", 
        description="执行策略：local-本地执行, fixed-固定节点, auto-自动选择, prefer-优先绑定节点"
    )
    bound_node_id: str | None = Field(None, description="绑定的执行节点ID（用于 fixed/prefer 策略）")
    fallback_enabled: bool = Field(True, description="是否启用故障转移（仅 prefer 策略时有效）")

    @field_validator('tags', mode='before')
    @classmethod
    def parse_tags(cls, v):
        """解析标签字段"""
        if v is None:
            return []
        if isinstance(v, str):
            # 如果是逗号分隔的字符串
            if v.startswith('[') and v.endswith(']'):
                # JSON数组格式
                result = JSONParser.parse_list(v, "tags")
                return result if result is not None else []
            else:
                # 逗号分隔格式
                return [tag.strip() for tag in v.split(',') if tag.strip()]
        return v

    @field_validator('dependencies', mode='before')
    @classmethod
    def parse_dependencies(cls, v):
        """解析依赖包字段"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_list(v, "dependencies")
        return v

    @field_validator('node_id')
    @classmethod
    def validate_node_id(cls, v, info):
        """验证节点ID"""
        env_location = info.data.get('env_location')
        if env_location == 'node' and not v:
            raise ValueError('使用节点环境时必须指定 node_id')
        return v

    @field_validator('existing_env_name')
    @classmethod
    def validate_existing_env(cls, v, info):
        """验证现有环境名称"""
        use_existing = info.data.get('use_existing_env')
        if use_existing and not v:
            raise ValueError('使用现有环境时必须指定 existing_env_name')
        return v

    @field_validator('python_version')
    @classmethod
    def validate_python_version(cls, v, info):
        """验证Python版本"""
        use_existing = info.data.get('use_existing_env')
        venv_scope = info.data.get('venv_scope')

        # 如果是创建新环境，必须指定版本
        if not use_existing and not v:
            raise ValueError('创建新环境时必须指定 python_version')

        # 私有环境必须指定有效版本
        if venv_scope == VenvScope.PRIVATE and not use_existing:
            if not v or not isinstance(v, str) or len(v) < 3:
                raise ValueError('私有环境必须提供有效的 Python 版本')

        return v


class ProjectFileCreateRequest(ProjectCreateRequest):
    """文件项目创建请求"""
    entry_point: str | None = Field(None, max_length=255, description="入口文件路径")
    runtime_config: dict | None = Field(None, description="运行时配置")
    environment_vars: dict | None = Field(None, description="环境变量")

    @field_validator('runtime_config', mode='before')
    @classmethod
    def parse_runtime_config(cls, v):
        """解析运行时配置"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_safely(v, "runtime_config")
        return v

    @field_validator('environment_vars', mode='before')
    @classmethod
    def parse_environment_vars(cls, v):
        """解析环境变量"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_safely(v, "environment_vars")
        return v


class BrowserConfig(BaseModel):
    """浏览器引擎配置"""
    headless: bool = Field(True, description="无头模式")
    no_imgs: bool = Field(False, description="禁用图片加载")
    mute: bool = Field(True, description="静音模式")
    incognito: bool = Field(False, description="匿名/隐私模式")
    window_size: str | None = Field("1920,1080", description="窗口大小")
    page_load_timeout: int = Field(30, description="页面加载超时(秒)")
    user_agent: str | None = Field(None, description="自定义 User-Agent")
    extra_arguments: str | None = Field(None, description="自定义启动参数，逗号分隔")


class ProjectRuleCreateRequest(ProjectCreateRequest):
    """规则项目创建请求"""
    engine: CrawlEngine = Field(CrawlEngine.REQUESTS, description="采集引擎")
    target_url: str = Field(..., max_length=2000, description="目标URL")
    url_pattern: str | None = Field(None, max_length=500, description="URL匹配模式")
    request_method: RequestMethod = Field(RequestMethod.GET, description="请求方法")
    callback_type: CallbackType = Field(CallbackType.LIST, description="回调类型")
    extraction_rules: list[ExtractionRule] | None = Field(None, description="提取规则数组")
    pagination_config: PaginationConfig | None = Field(None, description="分页配置")
    max_pages: int = Field(10, ge=1, le=1000, description="最大页数")
    start_page: int = Field(1, ge=1, description="起始页码")
    request_delay: int = Field(1000, ge=0, description="请求间隔(ms)")
    priority: int = Field(0, description="优先级")
    headers: dict | None = Field(None, description="请求头")
    cookies: dict | None = Field(None, description="Cookie")
    browser_config: BrowserConfig | None = Field(None, description="浏览器引擎配置（仅当engine=browser时有效）")

    @field_validator('extraction_rules', mode='before')
    @classmethod
    def parse_extraction_rules(cls, v):
        """解析提取规则JSON字符串 - 支持单引号和双引号"""
        if v is None:
            return None
        if isinstance(v, str):
            parsed = JSONParser.parse_extraction_rules(v, "extraction_rules")
            if parsed is None:
                raise ValueError('extraction_rules JSON格式错误')

            try:
                # 转换为ExtractionRule对象列表
                return [ExtractionRule(**rule) if isinstance(rule, dict) else rule for rule in parsed]
            except Exception as e:
                raise ValueError(f'extraction_rules 对象转换错误: {str(e)}')
        return v

    @field_validator('pagination_config', mode='before')
    @classmethod
    def parse_pagination_config(cls, v):
        """解析分页配置JSON字符串 - 支持单引号和双引号"""
        if v is None:
            return None
        if isinstance(v, str):
            parsed = JSONParser.parse_pagination_config(v, "pagination_config")
            if parsed is None:
                raise ValueError('pagination_config JSON格式错误')

            try:
                return PaginationConfig(**parsed) if isinstance(parsed, dict) else parsed
            except Exception as e:
                raise ValueError(f'pagination_config 对象转换错误: {str(e)}')
        return v

    @field_validator('headers', 'cookies', mode='before')
    @classmethod
    def parse_json_fields(cls, v):
        """解析JSON字段 (headers, cookies) - 支持单引号和双引号"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_safely(v, "json_field")
        return v

    @field_validator('extraction_rules')
    @classmethod
    def validate_extraction_rules(cls, v):
        """验证提取规则"""
        if not v:
            raise ValueError('必须提供提取规则')
        return v


class ProjectCodeCreateRequest(ProjectCreateRequest):
    """代码项目创建请求"""
    language: Optional[str] = Field("python", max_length=50, description="编程语言")
    version: Optional[str] = Field("1.0.0", max_length=20, description="版本号")
    entry_point: Optional[str] = Field(None, max_length=255, description="入口函数")
    documentation: Optional[str] = Field(None, description="代码文档")
    code_content: Optional[str] = Field(None, description="代码内容（直接提交代码时使用）")


class ProjectUpdateRequest(BaseModel):
    """项目更新请求"""
    name: Optional[str] = Field(None, min_length=3, max_length=50, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")
    status: Optional[ProjectStatus] = Field(None, description="项目状态")
    tags: Optional[List[str]] = Field(None, description="项目标签")
    dependencies: Optional[List[str]] = Field(None, description="Python依赖包")

    # 区域配置
    region: Optional[str] = Field(None, description="执行区域")

    # 执行策略配置（保留兼容）
    execution_strategy: Optional[str] = Field(None, description="执行策略")
    bound_node_id: Optional[str] = Field(None, description="绑定的执行节点ID")
    fallback_enabled: Optional[bool] = Field(None, description="是否启用故障转移")


# Form参数模型（用于multipart/form-data请求）
class ProjectCreateFormRequest(BaseModel):
    """项目创建Form请求模式（用于文件上传）"""
    # 通用参数
    name: str = Field(..., min_length=3, max_length=50, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")
    type: ProjectType = Field(..., description="项目类型")
    tags: Optional[str] = Field(None, description="项目标签，逗号分隔或JSON数组")
    dependencies: Optional[str] = Field(None, description="Python依赖包JSON数组")
    venv_scope: VenvScope = Field(..., description="虚拟环境作用域：shared/private")
    python_version: Optional[str] = Field(None, max_length=20, description="Python版本（私有环境必填）")
    shared_venv_key: Optional[str] = Field(None, description="共享环境标识（可选）")
    interpreter_source: Optional[str] = Field("mise", description="解释器来源：mise/local（私有环境时使用）")
    python_bin: Optional[str] = Field(None, description="当来源为local时的python路径（私有环境时使用）")

    # 节点环境参数
    env_location: Optional[str] = Field("local", description="环境位置：local/node")
    node_id: Optional[str] = Field(None, description="节点ID（使用节点环境时必填）")
    use_existing_env: Optional[bool] = Field(False, description="是否使用现有环境")
    existing_env_name: Optional[str] = Field(None, description="现有环境名称")
    env_name: Optional[str] = Field(None, description="新环境名称")
    env_description: Optional[str] = Field(None, description="环境描述")

    # 文件项目参数
    entry_point: Optional[str] = Field(None, max_length=255, description="入口文件路径")
    runtime_config: Optional[str] = Field(None, description="运行时配置JSON")
    environment_vars: Optional[str] = Field(None, description="环境变量JSON")

    # 区域配置
    region: Optional[str] = Field(None, description="执行区域")

    # 规则项目参数
    engine: Optional[str] = Field("requests", description="采集引擎 (browser/requests/curl_cffi)")
    target_url: Optional[str] = Field(None, max_length=2000, description="目标URL")
    url_pattern: Optional[str] = Field(None, max_length=500, description="URL匹配模式")
    request_method: Optional[str] = Field("GET", description="请求方法 (GET/POST/PUT/DELETE)")
    callback_type: Optional[str] = Field("list", description="回调类型 (list/detail)")
    extraction_rules: Optional[str] = Field(None, description="提取规则数组JSON")
    pagination_config: Optional[str] = Field(None, description="分页配置JSON")
    max_pages: Optional[int] = Field(10, ge=1, le=1000, description="最大页数")
    start_page: Optional[int] = Field(1, ge=1, description="起始页码")
    request_delay: Optional[int] = Field(1000, ge=0, description="请求间隔(ms)")
    priority: Optional[int] = Field(0, description="优先级")
    headers: Optional[str] = Field(None, description="请求头JSON")
    cookies: Optional[str] = Field(None, description="Cookie JSON")
    browser_config: Optional[str] = Field(None, description="浏览器引擎配置JSON")

    # 代码项目参数
    language: Optional[str] = Field("python", max_length=50, description="编程语言")
    version: Optional[str] = Field("1.0.0", max_length=20, description="版本号")
    code_entry_point: Optional[str] = Field(None, max_length=255, description="入口函数")
    documentation: Optional[str] = Field(None, description="代码文档")
    code_content: Optional[str] = Field(None, description="代码内容（直接提交代码时使用）")


class ProjectListQueryRequest(BaseModel):
    """项目列表查询参数模式"""
    page: int = Field(1, ge=1, description="页码")
    size: int = Field(20, ge=1, le=500, description="每页数量")
    type: Optional[ProjectType] = Field(None, description="项目类型筛选")
    status: Optional[ProjectStatus] = Field(None, description="项目状态筛选")
    tag: Optional[str] = Field(None, description="标签筛选")
    created_by: Optional[str] = Field(None, description="创建者公开ID筛选")
    search: Optional[str] = Field(None, description="关键字搜索（名称、描述）")


class TaskMeta(BaseModel):
    """任务元数据模型"""
    fetch_type: CrawlEngine = Field(..., description="爬取方式")
    pagination: Optional[PaginationConfig] = Field(None, description="分页配置")
    rules: List[ExtractionRule] = Field(..., description="提取规则列表")
    page_number: Optional[int] = Field(None, description="当前页码")
    proxy: Optional[str] = Field(None, description="代理配置")
    task_id: Optional[str] = Field(None, description="任务ID")
    worker_id: Optional[str] = Field(None, description="工作节点ID")


class TaskJsonRequest(BaseModel):
    """任务JSON请求模型"""
    url: str = Field(..., description="目标URL")
    callback: CallbackType = Field(..., description="回调类型")
    method: RequestMethod = Field(RequestMethod.GET, description="请求方法")
    meta: TaskMeta = Field(..., description="任务元数据")
    headers: Optional[Dict[str, str]] = Field(None, description="请求头")
    cookies: Optional[Dict[str, str]] = Field(None, description="Cookie")
    priority: int = Field(0, description="优先级")
    dont_filter: bool = Field(False, description="是否去重")


class ProjectRuleUpdateRequest(BaseModel):
    """规则项目更新请求模型"""
    target_url: Optional[str] = Field(None, max_length=2000, description="目标URL")
    callback_type: Optional[CallbackType] = Field(None, description="回调类型")
    request_method: Optional[RequestMethod] = Field(None, description="请求方法")
    extraction_rules: Optional[List[ExtractionRule]] = Field(None, description="提取规则数组")
    pagination_config: Optional[PaginationConfig] = Field(None, description="分页配置")
    max_pages: Optional[int] = Field(None, ge=1, le=1000, description="最大页数")
    start_page: Optional[int] = Field(None, ge=1, description="起始页码")
    request_delay: Optional[int] = Field(None, ge=0, description="请求间隔(ms)")
    priority: Optional[int] = Field(None, description="优先级")
    dont_filter: Optional[bool] = Field(None, description="是否去重")
    headers: Optional[Dict[str, str]] = Field(None, description="请求头")
    cookies: Optional[Dict[str, str]] = Field(None, description="Cookie")
    proxy_config: Optional[str] = Field(None, description="代理配置")
    task_config: Optional[Dict[str, Any]] = Field(None, description="任务配置")


class ProjectCodeUpdateRequest(BaseModel):
    """代码项目更新请求模型"""
    language: Optional[str] = Field(None, max_length=50, description="编程语言")
    version: Optional[str] = Field(None, max_length=20, description="版本号")
    entry_point: Optional[str] = Field(None, max_length=255, description="入口函数")
    documentation: Optional[str] = Field(None, description="代码文档")
    code_content: Optional[str] = Field(None, description="代码内容")


class ProjectFileUpdateRequest(BaseModel):
    """文件项目更新请求模型"""
    entry_point: Optional[str] = Field(None, max_length=255, description="入口文件路径")
    runtime_config: Optional[Union[str, Dict[str, Any]]] = Field(None, description="运行时配置")
    environment_vars: Optional[Union[str, Dict[str, Any]]] = Field(None, description="环境变量")

    @field_validator('runtime_config', mode='before')
    @classmethod
    def parse_runtime_config(cls, v):
        """解析运行时配置"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_safely(v, "runtime_config")
        return v

    @field_validator('environment_vars', mode='before')
    @classmethod
    def parse_environment_vars(cls, v):
        """解析环境变量"""
        if v is None:
            return None
        if isinstance(v, str):
            return JSONParser.parse_safely(v, "environment_vars")
        return v


class ProjectFileContentUpdateRequest(BaseModel):
    """文件内容更新请求模型"""
    file_path: str = Field(..., max_length=1024, description="文件相对路径")
    content: str = Field(..., description="文件内容")
    encoding: Optional[str] = Field("utf-8", max_length=50, description="文件编码")

    @field_validator('file_path')
    @classmethod
    def validate_file_path(cls, value: str) -> str:
        if value is None:
            raise ValueError("文件路径不能为空")

        sanitized = value.strip()

        # 允许空字符串，表示基础文件路径
        if sanitized == "" and value == "":
            return ""

        if sanitized == "":
            raise ValueError("文件路径不能为空")

        normalized = sanitized.replace('\\', '/')
        if normalized.startswith('/'):
            raise ValueError("文件路径不合法")

        if normalized.startswith('..') or '..' in normalized.split('/'):
            raise ValueError("文件路径不合法")

        return normalized


class FileInfo(BaseModel):
    """文件信息"""
    original_name: str = Field(description="原始文件名")
    file_size: int = Field(description="文件大小")
    file_hash: str = Field(description="文件哈希")
    file_path: Optional[str] = Field(None, description="文件存储路径")
    file_type: Optional[str] = Field(None, description="文件类型")
    storage_type: Optional[str] = Field(None, description="存储类型")
    entry_point: Optional[str] = Field(None, description="入口文件")
    runtime_config: Optional[Dict[str, Any]] = Field(None, description="运行时配置")
    environment_vars: Optional[Dict[str, Any]] = Field(None, description="环境变量")
    is_compressed: Optional[bool] = Field(None, description="是否为压缩包")
    original_file_path: Optional[str] = Field(None, description="原始文件路径")


class FileTreeNode(BaseModel):
    """文件树节点"""
    name: str = Field(description="文件/目录名")
    type: str = Field(description="类型: file 或 directory")
    path: str = Field(description="相对路径")
    size: int = Field(description="文件大小")
    modified_time: Optional[float] = Field(None, description="修改时间戳")
    mime_type: Optional[str] = Field(None, description="MIME类型")
    is_text: Optional[bool] = Field(None, description="是否为文本文件")
    children: Optional[List['FileTreeNode']] = Field(None, description="子节点")
    children_count: Optional[int] = Field(None, description="子节点数量")
    error: Optional[str] = Field(None, description="错误信息")
    truncated: Optional[bool] = Field(None, description="是否被截断")


class FileStructureResponse(BaseModel):
    """文件结构响应"""
    project_id: str = Field(description="项目公开ID")
    project_name: str = Field(description="项目名称")
    file_path: str = Field(description="文件路径")
    structure: FileTreeNode = Field(description="文件结构树")
    total_files: int = Field(description="总文件数")
    total_size: int = Field(description="总大小")


class FileContentResponse(BaseModel):
    """文件内容响应"""
    name: str = Field(description="文件名")
    path: str = Field(description="文件路径")
    size: int = Field(description="文件大小")
    modified_time: float = Field(description="修改时间戳")
    mime_type: str = Field(description="MIME类型")
    is_text: bool = Field(description="是否为文本文件")
    content: Optional[str] = Field(None, description="文件内容")
    encoding: Optional[str] = Field("utf-8", description="文件编码")
    error: Optional[str] = Field(None, description="错误信息")
    too_large: Optional[bool] = Field(None, description="文件是否过大")
    binary: Optional[bool] = Field(None, description="是否为二进制文件")


class ProjectResponse(BaseModel):
    """项目响应模式"""
    id: str = Field(description="项目公开ID")
    name: str = Field(description="项目名称")
    description: Optional[str] = Field(description="项目描述")
    type: ProjectType = Field(description="项目类型")
    status: ProjectStatus = Field(description="项目状态")
    tags: List[str] = Field(description="项目标签")
    dependencies: Optional[List[str]] = Field(description="Python依赖包")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    created_by: Optional[str] = Field(None, description="创建者公开ID")
    created_by_username: Optional[str] = Field(None, description="创建者用户名")
    download_count: int = Field(description="下载次数")
    star_count: int = Field(description="收藏次数")
    # 运行环境
    python_version: Optional[str] = Field(None, description="绑定的Python版本")
    venv_scope: Optional[VenvScope] = Field(None, description="虚拟环境作用域")
    venv_path: Optional[str] = Field(None, description="虚拟环境路径")
    file_info: Optional[FileInfo] = Field(None, description="文件信息")
    rule_info: Optional[Dict[str, Any]] = Field(None, description="规则信息")
    code_info: Optional[Dict[str, Any]] = Field(None, description="代码信息")

    # 执行策略配置
    execution_strategy: Optional[str] = Field(None, description="执行策略")
    bound_node_id: Optional[str] = Field(None, description="绑定的执行节点ID")
    bound_node_name: Optional[str] = Field(None, description="绑定的执行节点名称")
    fallback_enabled: Optional[bool] = Field(None, description="是否启用故障转移")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ProjectListResponse(BaseModel):
    """项目列表响应模式"""
    id: str = Field(description="项目公开ID")
    name: str = Field(description="项目名称")
    description: Optional[str] = Field(description="项目描述")
    type: ProjectType = Field(description="项目类型")
    status: ProjectStatus = Field(description="项目状态")
    tags: List[str] = Field(description="项目标签")
    created_at: datetime = Field(description="创建时间")
    created_by: Optional[str] = Field(None, description="创建者公开ID")
    created_by_username: Optional[str] = Field(None, description="创建者用户名")
    download_count: int = Field(description="下载次数")
    star_count: int = Field(description="收藏次数")

    model_config = ConfigDict(from_attributes=True)


# 更新FileTreeNode模型以支持前向引用
FileTreeNode.model_rebuild()
