"""
统一的项目更新Schema
支持所有项目类型的字段更新
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from antcode_core.common.utils.json_parser import JSONParser
from antcode_core.domain.models.enums import CallbackType, CrawlEngine, ProjectStatus, RequestMethod


class UnifiedProjectUpdateRequest(BaseModel):
    """统一的项目更新请求 - 支持所有项目类型"""

    model_config = ConfigDict(extra="forbid")

    # ======= 基本信息字段 (所有项目类型) =======
    name: str | None = Field(None, min_length=3, max_length=50, description="项目名称")
    description: str | None = Field(None, max_length=500, description="项目描述")
    status: ProjectStatus | None = Field(None, description="项目状态")
    tags: list[str] | None = Field(None, description="项目标签")
    dependencies: list[str] | None = Field(None, description="Python依赖包")

    # ======= 执行策略字段 =======
    execution_strategy: str | None = Field(None, description="执行策略：fixed/auto/prefer/specified")
    bound_worker_id: str | None = Field(None, description="绑定的执行 Worker ID")

    # ======= 规则项目字段 (type=rule时使用) =======
    engine: CrawlEngine | None = Field(None, description="采集引擎")
    target_url: str | None = Field(None, max_length=2000, description="目标URL")
    url_pattern: str | None = Field(None, max_length=500, description="URL匹配模式")
    callback_type: CallbackType | None = Field(None, description="回调类型")
    request_method: RequestMethod | None = Field(None, description="请求方法")
    extraction_rules: str | list[dict[str, Any]] | None = Field(None, description="提取规则数组(JSON字符串或对象)")
    data_schema: str | dict[str, Any] | None = Field(None, description="数据结构定义(JSON字符串或对象)")
    pagination_config: str | dict[str, Any] | None = Field(None, description="分页配置(JSON字符串或对象)")
    max_pages: int | None = Field(None, ge=1, le=1000, description="最大页数")
    start_page: int | None = Field(None, ge=1, description="起始页码")
    request_delay: int | None = Field(None, ge=0, description="请求间隔(ms)")
    retry_count: int | None = Field(None, ge=0, le=10, description="重试次数")
    timeout: int | None = Field(None, ge=1, le=300, description="超时时间(s)")
    priority: int | None = Field(None, description="优先级")
    dont_filter: bool | None = Field(None, description="是否去重")
    headers: str | dict[str, Any] | None = Field(None, description="请求头(JSON字符串或对象)")
    cookies: str | dict[str, Any] | None = Field(None, description="Cookie(JSON字符串或对象)")
    proxy_config: str | dict[str, Any] | None = Field(None, description="代理配置(JSON字符串或对象)")
    anti_spider: str | dict[str, Any] | None = Field(None, description="反爬虫配置(JSON字符串或对象)")
    task_config: str | dict[str, Any] | None = Field(None, description="任务配置(JSON字符串或对象)")
    # S10 (Scrapy 迁移收尾)
    resume_enabled: bool | None = Field(None, description="scrapy-redis 断点续爬 + 分布式")
    dedup_config: str | dict[str, Any] | None = Field(None, description="内容级去重配置(JSON字符串或对象)")

    # ======= 文件项目字段 (type=file时使用) =======
    entry_point: str | None = Field(None, max_length=255, description="入口文件路径")
    runtime_config: str | dict[str, Any] | None = Field(None, description="运行时配置(JSON字符串或对象)")
    environment_vars: str | dict[str, Any] | None = Field(None, description="环境变量(JSON字符串或对象)")

    # ======= 代码项目字段 (type=code时使用) =======
    language: str | None = Field(None, max_length=50, description="编程语言")
    code_entry_point: str | None = Field(None, max_length=255, description="入口文件")
    documentation: str | None = Field(None, description="代码文档")

    # JSON字段解析validators - 使用统一的 JSONParser
    @field_validator("extraction_rules", mode="before")
    @classmethod
    def parse_extraction_rules(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return []
            return JSONParser.parse_list(v, "extraction_rules")
        return v

    @field_validator("pagination_config", mode="before")
    @classmethod
    def parse_pagination_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "pagination_config")
        return v

    @field_validator("data_schema", mode="before")
    @classmethod
    def parse_data_schema(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "data_schema")
        return v

    @field_validator("headers", mode="before")
    @classmethod
    def parse_headers(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "headers")
        return v

    @field_validator("cookies", mode="before")
    @classmethod
    def parse_cookies(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "cookies")
        return v

    @field_validator("proxy_config", mode="before")
    @classmethod
    def parse_proxy_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "proxy_config")
        return v

    @field_validator("anti_spider", mode="before")
    @classmethod
    def parse_anti_spider(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "anti_spider")
        return v

    @field_validator("task_config", mode="before")
    @classmethod
    def parse_task_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "task_config")
        return v

    @field_validator("dedup_config", mode="before")
    @classmethod
    def parse_dedup_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "dedup_config")
        return v

    @field_validator("runtime_config", mode="before")
    @classmethod
    def parse_runtime_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "runtime_config")
        return v

    @field_validator("environment_vars", mode="before")
    @classmethod
    def parse_environment_vars(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v.strip() == "":
                return {}
            return JSONParser.parse_safely(v, "environment_vars")
        return v

    def get_basic_fields(self):
        """获取基本信息字段"""
        basic_fields = [
            "name",
            "description",
            "status",
            "tags",
            "dependencies",
            "execution_strategy",
            "bound_worker_id",
        ]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in basic_fields}

    def get_rule_fields(self):
        """获取规则项目字段"""
        rule_fields = [
            "engine",
            "target_url",
            "url_pattern",
            "callback_type",
            "request_method",
            "extraction_rules",
            "data_schema",
            "pagination_config",
            "max_pages",
            "start_page",
            "request_delay",
            "retry_count",
            "timeout",
            "priority",
            "dont_filter",
            "headers",
            "cookies",
            "proxy_config",
            "anti_spider",
            "task_config",
            # S10
            "resume_enabled",
            "dedup_config",
        ]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in rule_fields}

    def get_file_fields(self):
        """获取文件项目字段"""
        file_fields = ["entry_point", "runtime_config", "environment_vars"]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in file_fields}

    def get_code_fields(self):
        """获取代码项目字段"""
        code_fields = [
            "language",
            "code_entry_point",
            "documentation",
            "runtime_config",
            "environment_vars",
        ]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in code_fields}
