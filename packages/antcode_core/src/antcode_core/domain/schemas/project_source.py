"""Project source API schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProjectSourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str = Field(..., max_length=32)
    ref: str = Field("main", max_length=255)
    subdir: str = Field(..., max_length=500)
    include_paths: list[str] = Field(default_factory=list)


class ProjectImportItem(ProjectSourcePayload):
    name: str = Field(..., min_length=3, max_length=50)
    description: str | None = Field(None, max_length=500)
    tags: list[str] = Field(default_factory=list)
    dependencies: list[str] | None = None
    runtime_scope: str
    runtime_kind: str = "python"
    python_version: str | None = None
    worker_id: str | None = None
    execution_strategy: str = "auto"
    bound_worker_id: str | None = None
    entry_point: str = Field(..., max_length=255)
    runtime_config: dict | None = None


class ProjectSourceResponse(BaseModel):
    project_id: str
    repository_id: str
    repository_name: str
    repository_url: str
    ref: str
    subdir: str
    include_paths: list[str]
    resolved_commit: str | None = None
