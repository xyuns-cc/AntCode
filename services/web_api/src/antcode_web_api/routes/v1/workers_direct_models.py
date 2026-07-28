"""Validated request models for Direct Worker control operations."""

from typing import Any, Literal

from antcode_core.spider_ingest import DEFAULT_MAX_BATCH_ITEMS
from pydantic import BaseModel, ConfigDict, Field

MAX_RUN_ID_LENGTH = 64
MAX_LEASE_ID_LENGTH = 64
MAX_RUN_OWNERSHIP_TTL_MS = 3_900_000


class DirectLeaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: float | None = Field(default=None, ge=0, le=100)
    memory: float | None = Field(default=None, ge=0, le=100)
    disk: float | None = Field(default=None, ge=0, le=100)
    running_tasks: int | None = Field(default=None, ge=0)
    max_concurrent_tasks: int | None = Field(default=None, ge=1)


class DirectLeaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    operation: Literal["lease"]
    current_lease_id: str = Field(default="", max_length=MAX_LEASE_ID_LENGTH)
    metrics: DirectLeaseMetrics | None = None


class DirectOwnershipIdentityRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    lease_id: str = Field(min_length=1, max_length=MAX_LEASE_ID_LENGTH)
    run_id: str = Field(min_length=1, max_length=MAX_RUN_ID_LENGTH)


class DirectOwnershipClaimRequest(DirectOwnershipIdentityRequest):
    operation: Literal["claim"]
    ttl_ms: int = Field(gt=0, le=MAX_RUN_OWNERSHIP_TTL_MS)


class DirectOwnershipRenewRequest(DirectOwnershipIdentityRequest):
    operation: Literal["renew"]
    ttl_ms: int = Field(gt=0, le=MAX_RUN_OWNERSHIP_TTL_MS)


class DirectOwnershipReleaseRequest(DirectOwnershipIdentityRequest):
    operation: Literal["release"]


class DirectDeregisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    operation: Literal["deregister"]
    lease_id: str = Field(min_length=1, max_length=MAX_LEASE_ID_LENGTH)
    reason: str = Field(default="shutdown", max_length=128)


class DirectSpiderIdentityRequest(DirectOwnershipIdentityRequest):
    project_id: str = Field(min_length=1, max_length=MAX_RUN_ID_LENGTH)


class DirectSpiderItemsRequest(DirectSpiderIdentityRequest):
    operation: Literal["spider-items"]
    items: list[dict[str, Any]] = Field(min_length=1, max_length=DEFAULT_MAX_BATCH_ITEMS)


class DirectSpiderMetaRequest(DirectSpiderIdentityRequest):
    operation: Literal["spider-meta"]
    meta: dict[str, Any] = Field(min_length=1)


__all__ = [
    "DirectDeregisterRequest",
    "DirectLeaseRequest",
    "DirectOwnershipClaimRequest",
    "DirectOwnershipIdentityRequest",
    "DirectOwnershipReleaseRequest",
    "DirectOwnershipRenewRequest",
    "DirectSpiderItemsRequest",
    "DirectSpiderMetaRequest",
]
