"""Git repository API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from antcode_core.domain.models.enums import ExecutionStrategy, RuntimeKind, RuntimeScope


class RepositoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., max_length=2000)
    default_ref: str = Field("main", max_length=255)
    credential_id: str | None = Field(None, max_length=32)


class RepositoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    url: str | None = Field(None, max_length=2000)
    default_ref: str | None = Field(None, max_length=255)
    credential_id: str | None = Field(None, max_length=32)
    enabled: bool | None = None


class RepositoryScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str | None = Field(None, max_length=255)


class RepositoryCandidateResponse(BaseModel):
    subdir: str
    entry_point: str
    markers: list[str] = Field(default_factory=list)


class RepositoryResponse(BaseModel):
    id: str
    name: str
    url: str
    default_ref: str
    credential_id: str | None = None
    enabled: bool
    last_scan_status: str | None = None
    last_scan_error: str | None = None
    last_scan_result: list[dict[str, Any]] | None = None
    last_scanned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RepositoryScanResponse(BaseModel):
    repository_id: str
    ref: str
    candidates: list[RepositoryCandidateResponse]


# O4: 从仓库导入项目 — 前端 repositories.ts.importFromRepository 对应契约。
# 服务层 project_source_service.import_projects 已就绪，此处补路由层 schema。
class ProjectImportItem(BaseModel):
    """单个项目导入项。字段与前端 types/repository.ts::ProjectImportItem 对齐。"""

    model_config = ConfigDict(extra="ignore")

    repository_id: str = Field(..., max_length=32)
    ref: str = Field("", max_length=255)
    subdir: str = Field("", max_length=1024)
    entry_point: str = Field("", max_length=1024)
    include_paths: list[str] = Field(default_factory=list)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1024)
    tags: list[str] = Field(default_factory=list)
    dependencies: list[str] | None = None
    runtime_scope: RuntimeScope = Field(RuntimeScope.PRIVATE)
    runtime_kind: RuntimeKind = Field(RuntimeKind.PYTHON)
    python_version: str = Field(..., max_length=32, pattern=r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
    worker_id: str = Field(..., min_length=1, max_length=32)
    execution_strategy: ExecutionStrategy = Field(ExecutionStrategy.FIXED_WORKER)
    bound_worker_id: str = Field(..., min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> ProjectImportItem:
        if self.runtime_scope != RuntimeScope.PRIVATE:
            raise ValueError("仓库导入仅支持新建私有运行时")
        if self.runtime_kind != RuntimeKind.PYTHON:
            raise ValueError("仓库导入仅支持 Python 运行时")
        if self.execution_strategy != ExecutionStrategy.FIXED_WORKER:
            raise ValueError("仓库导入项目必须固定到运行时所在 Worker")
        if self.worker_id != self.bound_worker_id:
            raise ValueError("fixed 策略的运行时 Worker 与绑定 Worker 必须一致")
        return self


class ImportProjectsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: list[ProjectImportItem] = Field(..., min_length=1, max_length=100)

    @field_validator("projects")
    @classmethod
    def reject_duplicate_projects(cls, value: list[ProjectImportItem]) -> list[ProjectImportItem]:
        seen: set[tuple[str, str, str, str]] = set()
        for item in value:
            key = (item.repository_id, item.ref, item.subdir, item.name)
            if key in seen:
                raise ValueError("projects 包含重复导入项")
            seen.add(key)
        return value


class ImportProjectsResult(BaseModel):
    created: list[str]
