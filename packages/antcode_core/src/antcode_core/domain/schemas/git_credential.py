"""Git 凭证 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


SUPPORTED_AUTH_TYPES = {"token", "basic"}


def _validate_basic_username(auth_type: str | None, username: str | None) -> None:
    if auth_type != "basic":
        return
    if username and username.strip():
        return
    raise ValueError("Basic 认证必须提供 username")


class GitCredentialCreateRequest(BaseModel):
    """创建 Git 凭证请求。"""

    name: str = Field(..., min_length=1, max_length=100)
    auth_type: str = Field(..., min_length=1, max_length=20)
    username: str | None = Field(None, max_length=255)
    secret: str = Field(..., min_length=1)
    host_scope: str = Field(..., min_length=1, max_length=255)

    @field_validator("auth_type")
    @classmethod
    def validate_auth_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_AUTH_TYPES:
            raise ValueError("auth_type 仅支持 token/basic")
        return normalized

    @field_validator("host_scope")
    @classmethod
    def normalize_host_scope(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_username_requirement(self):
        _validate_basic_username(self.auth_type, self.username)
        return self


class GitCredentialUpdateRequest(BaseModel):
    """更新 Git 凭证请求。"""

    name: str | None = Field(None, min_length=1, max_length=100)
    auth_type: str | None = Field(None, min_length=1, max_length=20)
    username: str | None = Field(None, max_length=255)
    secret: str | None = Field(None, min_length=1)
    host_scope: str | None = Field(None, min_length=1, max_length=255)

    @field_validator("auth_type")
    @classmethod
    def validate_auth_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_AUTH_TYPES:
            raise ValueError("auth_type 仅支持 token/basic")
        return normalized

    @field_validator("host_scope")
    @classmethod
    def normalize_host_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_username_requirement(self):
        if self.auth_type is not None:
            _validate_basic_username(self.auth_type, self.username)
        return self


class GitCredentialResponse(BaseModel):
    """Git 凭证响应。"""

    id: str = Field(...)
    name: str = Field(...)
    auth_type: str = Field(...)
    username: str | None = Field(None)
    host_scope: str = Field(...)
    has_secret: bool = Field(True)
    created_at: datetime = Field(...)
    updated_at: datetime = Field(...)


__all__ = [
    "GitCredentialCreateRequest",
    "GitCredentialResponse",
    "GitCredentialUpdateRequest",
    "SUPPORTED_AUTH_TYPES",
]
