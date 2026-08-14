"""Worker 手动派发接口的请求模型与参数上限常量。

只描述"外部输入的合法形状"，不依赖任何派发实现；``workers_dispatch`` 与
``workers`` 顶层都 re-export 这些模型，测试引用路径保持不变。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# 参数验证常量
MAX_DISPATCH_BATCH_TASKS = 500
MAX_DISPATCH_TIMEOUT_SECONDS = 86400
DEFAULT_DISPATCH_TIMEOUT_SECONDS = 3600
PRIORITY_MIN, PRIORITY_MAX = 0, 4

_MAX_ID_LENGTH = 64
_MAX_REGION_LENGTH = 50
_MAX_TAGS = 32
_MAX_BATCH_ID_LENGTH = 128
_PROJECT_TYPE_PATTERN = "^(code|file|rule)$"


class StrictRequestModel(BaseModel):
    """禁止未知字段的请求基类，并保留 dict 风格的 ``get`` 便于 handler 取值。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    def get(self, key: str, default=None):
        value = getattr(self, key, default)
        return default if value is None else value


class WorkerDispatchTaskRequest(StrictRequestModel):
    project_id: str = Field(..., min_length=1, max_length=_MAX_ID_LENGTH)
    task_id: int = Field(..., gt=0)
    params: dict = Field(default_factory=dict)
    environment_vars: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=DEFAULT_DISPATCH_TIMEOUT_SECONDS, ge=1, le=MAX_DISPATCH_TIMEOUT_SECONDS)
    worker_id: str | None = Field(default=None, min_length=1, max_length=_MAX_ID_LENGTH)
    region: str | None = Field(default=None, max_length=_MAX_REGION_LENGTH)
    tags: list[str] | None = Field(default=None, max_length=_MAX_TAGS)
    priority: int | None = Field(default=None, ge=PRIORITY_MIN, le=PRIORITY_MAX)
    project_type: str = Field(default="code", pattern=_PROJECT_TYPE_PATTERN)
    require_render: bool = False


class WorkerDispatchBatchTaskRequest(StrictRequestModel):
    project_id: str = Field(..., min_length=1, max_length=_MAX_ID_LENGTH)
    task_id: int = Field(..., gt=0)
    run_id: str = Field(..., min_length=1, max_length=_MAX_ID_LENGTH)
    params: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=DEFAULT_DISPATCH_TIMEOUT_SECONDS, ge=1, le=MAX_DISPATCH_TIMEOUT_SECONDS)
    priority: int | None = Field(default=None, ge=PRIORITY_MIN, le=PRIORITY_MAX)
    project_type: str = Field(default="code", pattern=_PROJECT_TYPE_PATTERN)
    require_render: bool = False


class WorkerDispatchBatchRequest(StrictRequestModel):
    tasks: list[WorkerDispatchBatchTaskRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_DISPATCH_BATCH_TASKS,
    )
    worker_id: str | None = Field(default=None, min_length=1, max_length=_MAX_ID_LENGTH)
    region: str | None = Field(default=None, max_length=_MAX_REGION_LENGTH)
    tags: list[str] | None = Field(default=None, max_length=_MAX_TAGS)
    batch_id: str | None = Field(default=None, max_length=_MAX_BATCH_ID_LENGTH)
    require_render: bool = False


__all__ = [
    "DEFAULT_DISPATCH_TIMEOUT_SECONDS",
    "MAX_DISPATCH_BATCH_TASKS",
    "MAX_DISPATCH_TIMEOUT_SECONDS",
    "PRIORITY_MAX",
    "PRIORITY_MIN",
    "StrictRequestModel",
    "WorkerDispatchBatchRequest",
    "WorkerDispatchBatchTaskRequest",
    "WorkerDispatchTaskRequest",
]
