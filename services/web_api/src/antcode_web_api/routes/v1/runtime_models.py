"""Runtime API request models and small mappers."""

from __future__ import annotations

import uuid
from typing import Any

from antcode_contracts.runtime_metadata import (
    RUNTIME_DESCRIPTION_MAX_LENGTH,
    RUNTIME_KEY_MAX_LENGTH,
    validate_runtime_description,
    validate_runtime_key,
)
from antcode_core.domain.models.enums import RuntimeScope
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

MAX_RUNTIME_PACKAGE_BATCH = 100


class _PackageListModel(BaseModel):
    packages: list[str] = Field(default_factory=list, max_length=MAX_RUNTIME_PACKAGE_BATCH, description="包列表")

    @field_validator("packages")
    @classmethod
    def reject_duplicate_packages(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("packages 不允许重复")
        return value


class CreateEnvRequest(_PackageListModel):
    scope: RuntimeScope = Field(..., description="运行时作用域")
    python_version: str = Field(
        ...,
        pattern=r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$",
        description="Python 版本",
    )
    env_name: str | None = Field(None, description="环境名称")


class PackageRequest(_PackageListModel):
    upgrade: bool = Field(False, description="是否升级安装")


class EnvUpdateRequest(BaseModel):
    key: str | None = Field(default=None, max_length=RUNTIME_KEY_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=RUNTIME_DESCRIPTION_MAX_LENGTH)

    @field_validator("key")
    @classmethod
    def validate_key_bytes(cls, value: str | None) -> str | None:
        return validate_runtime_key(value)

    @field_validator("description")
    @classmethod
    def validate_description_bytes(cls, value: str | None) -> str | None:
        return validate_runtime_description(value)


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
