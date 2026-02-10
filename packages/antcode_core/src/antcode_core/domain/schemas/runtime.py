"""
运行时环境 Schema

运行时环境相关的请求和响应模式。
"""

from pydantic import BaseModel, Field, field_validator

from antcode_core.domain.models.enums import RuntimeKind, RuntimeScope


def _normalize_runtime_scope(value):
    """规范化运行时环境作用域"""
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
    """规范化运行时类型"""
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


class PythonVersionListResponse(BaseModel):
    """Python 版本列表响应"""
    versions: list[str]


class InterpreterInfo(BaseModel):
    """解释器信息"""
    id: str = Field("", description="解释器公开ID")
    version: str
    install_dir: str
    python_bin: str
    source: str = ""


class InstallInterpreterRequest(BaseModel):
    """安装解释器请求"""
    version: str = Field(..., description="Python 版本，如 3.11.9")


class RuntimeStatusResponse(BaseModel):
    """运行时环境状态响应"""
    project_id: str = Field(description="项目公开ID")
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON, description="运行时类型")
    scope: str = ""
    version: str = ""
    runtime_locator: str = ""
    worker_id: str = Field("", description="环境所在的 Worker ID")


class CreateRuntimeRequest(BaseModel):
    """创建运行时环境请求"""
    version: str = Field(..., description="运行时版本，如 3.11.9")
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON, description="运行时类型")
    runtime_scope: RuntimeScope = Field(..., description="运行时环境作用域：shared/private")
    shared_runtime_key: str = Field("", description="共享运行时标识（可选）")
    create_if_missing: bool = Field(True, description="不存在则创建")
    interpreter_source: str = Field("mise", description="解释器来源：mise/local")
    python_bin: str = Field("", description="当来源为local时的python路径")

    @field_validator("runtime_scope", mode="before")
    @classmethod
    def normalize_runtime_scope(cls, v):
        return _normalize_runtime_scope(v)

    @field_validator("runtime_kind", mode="before")
    @classmethod
    def normalize_runtime_kind(cls, v):
        return _normalize_runtime_kind(v)


class CreateSharedRuntimeRequest(BaseModel):
    """创建共享运行时环境请求"""
    version: str = Field(..., description="运行时版本，如 3.11.9")
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON, description="运行时类型")
    shared_runtime_key: str = Field("", description="共享运行时标识（可选）")
    interpreter_source: str = Field("mise", description="解释器来源：mise/local")
    python_bin: str = Field("", description="当来源为local时的python路径")


class RuntimeListItem(BaseModel):
    """运行时环境列表项"""
    id: str = Field(description="运行时环境公开ID")
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON, description="运行时类型")
    scope: RuntimeScope
    key: str = ""
    version: str
    runtime_locator: str
    runtime_details: dict = Field(default_factory=dict)
    interpreter_version: str
    interpreter_source: str = ""
    python_bin: str
    install_dir: str
    created_by: str = Field("", description="创建者公开ID")
    created_by_username: str = ""
    created_at: str = ""
    updated_at: str = ""
    packages: list[dict] = Field(default_factory=list)
    current_project_id: str = Field("", description="当前项目公开ID")


__all__ = [
    "PythonVersionListResponse",
    "InterpreterInfo",
    "InstallInterpreterRequest",
    "RuntimeStatusResponse",
    "CreateRuntimeRequest",
    "CreateSharedRuntimeRequest",
    "RuntimeListItem",
]
