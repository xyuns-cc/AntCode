"""
统一的项目更新Schema
支持所有项目类型的字段更新
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, validator

from src.models.enums import ProjectStatus, CrawlEngine, CallbackType, RequestMethod


class UnifiedProjectUpdateRequest(BaseModel):
    """统一的项目更新请求 - 支持所有项目类型"""
    
    # ======= 基本信息字段 (所有项目类型) =======
    name: Optional[str] = Field(None, min_length=3, max_length=50, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")
    status: Optional[ProjectStatus] = Field(None, description="项目状态")
    tags: Optional[List[str]] = Field(None, description="项目标签")
    dependencies: Optional[List[str]] = Field(None, description="Python依赖包")
    
    # ======= 规则项目字段 (type=rule时使用) =======
    engine: Optional[CrawlEngine] = Field(None, description="采集引擎")
    target_url: Optional[str] = Field(None, max_length=2000, description="目标URL")
    url_pattern: Optional[str] = Field(None, max_length=500, description="URL匹配模式")
    callback_type: Optional[CallbackType] = Field(None, description="回调类型")
    request_method: Optional[RequestMethod] = Field(None, description="请求方法")
    extraction_rules: Optional[Union[str, List[Dict[str, Any]]]] = Field(None, description="提取规则数组(JSON字符串或对象)")
    data_schema: Optional[Union[str, Dict[str, Any]]] = Field(None, description="数据结构定义(JSON字符串或对象)")
    pagination_config: Optional[Union[str, Dict[str, Any]]] = Field(None, description="分页配置(JSON字符串或对象)")
    max_pages: Optional[int] = Field(None, ge=1, le=1000, description="最大页数")
    start_page: Optional[int] = Field(None, ge=1, description="起始页码")
    request_delay: Optional[int] = Field(None, ge=0, description="请求间隔(ms)")
    retry_count: Optional[int] = Field(None, ge=0, le=10, description="重试次数")
    timeout: Optional[int] = Field(None, ge=1, le=300, description="超时时间(s)")
    priority: Optional[int] = Field(None, description="优先级")
    dont_filter: Optional[bool] = Field(None, description="是否去重")
    headers: Optional[Union[str, Dict[str, Any]]] = Field(None, description="请求头(JSON字符串或对象)")
    cookies: Optional[Union[str, Dict[str, Any]]] = Field(None, description="Cookie(JSON字符串或对象)")
    proxy_config: Optional[Union[str, Dict[str, Any]]] = Field(None, description="代理配置(JSON字符串或对象)")
    anti_spider: Optional[Union[str, Dict[str, Any]]] = Field(None, description="反爬虫配置(JSON字符串或对象)")
    task_config: Optional[Union[str, Dict[str, Any]]] = Field(None, description="任务配置(JSON字符串或对象)")
    
    # ======= 文件项目字段 (type=file时使用) =======
    entry_point: Optional[str] = Field(None, max_length=255, description="入口文件路径")
    runtime_config: Optional[Union[str, Dict[str, Any]]] = Field(None, description="运行时配置(JSON字符串或对象)")
    environment_vars: Optional[Union[str, Dict[str, Any]]] = Field(None, description="环境变量(JSON字符串或对象)")
    
    # ======= 代码项目字段 (type=code时使用) =======
    content: Optional[str] = Field(None, description="代码内容")
    language: Optional[str] = Field(None, max_length=50, description="编程语言")
    version: Optional[str] = Field(None, max_length=20, description="版本号")
    code_entry_point: Optional[str] = Field(None, max_length=255, description="入口函数")
    documentation: Optional[str] = Field(None, description="代码文档")
    changelog: Optional[str] = Field(None, description="变更日志")

    class Config:
        extra = "ignore"  # 忽略额外字段，避免type等字段导致验证失败
    
    # JSON字段解析validators
    @validator('extraction_rules', pre=True)
    def parse_extraction_rules(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空列表
            if v.strip() == "":
                return []
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空列表
                return []
        return v
    
    @validator('pagination_config', pre=True)
    def parse_pagination_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
    
    @validator('data_schema', pre=True)
    def parse_data_schema(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
    
    @validator('headers', pre=True)
    def parse_headers(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                # 尝试直接解析
                return ujson.loads(v)
            except:
                try:
                    # 如果解析失败，尝试替换单引号为双引号
                    import re
                    # 替换单引号为双引号，但要注意字符串边界
                    fixed_v = re.sub(r"'([^']*)'", r'"\1"', v)
                    return ujson.loads(fixed_v)
                except:
                    # 如果还是失败，返回空字典
                    return {}
        return v
    
    @validator('cookies', pre=True)
    def parse_cookies(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                # 尝试直接解析
                return ujson.loads(v)
            except:
                try:
                    # 如果解析失败，尝试替换单引号为双引号
                    import re
                    # 替换单引号为双引号，但要注意字符串边界
                    fixed_v = re.sub(r"'([^']*)'", r'"\1"', v)
                    return ujson.loads(fixed_v)
                except:
                    # 如果还是失败，返回空字典
                    return {}
        return v
    
    @validator('proxy_config', pre=True)
    def parse_proxy_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
    
    @validator('anti_spider', pre=True)
    def parse_anti_spider(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
    
    @validator('task_config', pre=True)
    def parse_task_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
    
    @validator('runtime_config', pre=True)
    def parse_runtime_config(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
    
    @validator('environment_vars', pre=True)
    def parse_environment_vars(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # 如果是空字符串，返回空字典
            if v.strip() == "":
                return {}
            try:
                import ujson
                return ujson.loads(v)
            except:
                # 如果解析失败，返回空字典
                return {}
        return v
        
    def get_basic_fields(self):
        """获取基本信息字段"""
        basic_fields = ["name", "description", "status", "tags", "dependencies"]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in basic_fields}
    
    def get_rule_fields(self):
        """获取规则项目字段"""
        rule_fields = [
            "engine", "target_url", "url_pattern", "callback_type", "request_method",
            "extraction_rules", "data_schema", "pagination_config", "max_pages", 
            "start_page", "request_delay", "retry_count", "timeout", "priority",
            "dont_filter", "headers", "cookies", "proxy_config", "anti_spider", "task_config"
        ]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in rule_fields}
    
    def get_file_fields(self):
        """获取文件项目字段"""
        file_fields = ["entry_point", "runtime_config", "environment_vars"]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in file_fields}
    
    def get_code_fields(self):
        """获取代码项目字段"""
        code_fields = [
            "content", "language", "version", "code_entry_point", 
            "documentation", "changelog", "runtime_config", "environment_vars"
        ]
        return {k: v for k, v in self.dict(exclude_unset=True).items() if k in code_fields}
