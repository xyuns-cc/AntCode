"""Worker installation and registration schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from antcode_core.common.security.network_source import normalize_ip_or_cidr


class WorkerInstallKeyRequest(BaseModel):
    os_type: str = Field(..., description="操作系统类型: linux/macos/windows")
    allowed_source: str | None = Field(default=None, max_length=64, description="可选来源绑定（仅 IP/CIDR）")

    @field_validator("allowed_source")
    @classmethod
    def validate_allowed_source(cls, value: str | None) -> str | None:
        return normalize_ip_or_cidr(value)


class WorkerInstallKeyResponse(BaseModel):
    key: str
    os_type: str
    allowed_source: str | None = None
    install_command: str
    expires_at: datetime


class WorkerInstallKeyListItem(BaseModel):
    id: str
    os_type: str
    status: Literal["pending", "used", "expired", "revoked"]
    allowed_source: str | None = None
    used_by_worker: str | None = None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    registration_acknowledged_at: datetime | None = None


class _WorkerRegisterByKeyBase(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(8001, ge=1, le=65535)
    region: str = Field("", max_length=50)
    transport_mode: Literal["direct", "gateway"] = "gateway"
    client_timestamp: int
    client_nonce: str = Field(..., min_length=8, max_length=64)


class WorkerRegisterByKeyV2Request(_WorkerRegisterByKeyBase):
    registration_id: str = Field(..., min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    recovery_secret: SecretStr = Field(..., min_length=64, max_length=64)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("recovery_secret")
    @classmethod
    def validate_recovery_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) != 64 or any(character not in "0123456789abcdef" for character in secret):
            raise ValueError("recovery_secret 必须是 64 位小写十六进制字符串")
        return value


class WorkerRegisterByKeyV2Response(BaseModel):
    worker_id: str
    api_key: str
    secret_key: str
    registration_id: str
    protocol_version: Literal[2] = 2
    recovered: bool
    recovery_expires_at: datetime


class WorkerRegistrationAckRequest(BaseModel):
    registration_id: str = Field(..., min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


__all__ = [
    "WorkerInstallKeyRequest",
    "WorkerInstallKeyResponse",
    "WorkerInstallKeyListItem",
    "WorkerRegisterByKeyV2Request",
    "WorkerRegisterByKeyV2Response",
    "WorkerRegistrationAckRequest",
]
