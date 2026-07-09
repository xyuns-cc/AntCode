"""Runtime API request models and small mappers."""

from __future__ import annotations

import uuid
from typing import Any

from antcode_core.domain.models.enums import RuntimeScope
from fastapi import HTTPException
from pydantic import BaseModel, Field


class CreateEnvRequest(BaseModel):
    scope: RuntimeScope = Field(..., description="运行时作用域")
    python_version: str = Field(..., description="Python 版本")
    env_name: str | None = Field(None, description="环境名称")
    packages: list[str] = Field(default_factory=list, description="初始依赖")


class PackageRequest(BaseModel):
    packages: list[str] = Field(default_factory=list, description="包列表")
    upgrade: bool = Field(False, description="是否升级安装")


class EnvUpdateRequest(BaseModel):
    key: str | None = None
    description: str | None = None


def resolve_env_name(scope: str, python_version: str, env_name: str | None) -> str:
    version_suffix = python_version.replace(".", "")
    if scope == RuntimeScope.SHARED.value:
        if env_name and not env_name.startswith("shared-"):
            raise HTTPException(status_code=400, detail="共享环境名称必须以 shared- 开头")
        return env_name or f"shared-py{version_suffix}"

    if env_name and env_name.startswith("shared-"):
        raise HTTPException(status_code=400, detail="私有环境名称不允许以 shared- 开头")
    return env_name or f"private-{uuid.uuid4().hex[:8]}-py{version_suffix}"


def build_platform_info(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "os_type": data.get("system") or "",
        "os_version": data.get("release") or "",
        "python_version": data.get("python_version") or "",
        "machine": data.get("machine") or "",
        "mise_available": bool(data.get("mise_available", False)),
    }
