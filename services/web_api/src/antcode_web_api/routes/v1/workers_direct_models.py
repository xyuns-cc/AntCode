"""Validated request models for Direct Worker control operations."""

from typing import Annotated, Any, Literal

from antcode_core.spider_ingest import DEFAULT_MAX_BATCH_ITEMS
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_RUN_ID_LENGTH = 64
MAX_LEASE_ID_LENGTH = 64
MAX_RUN_OWNERSHIP_TTL_MS = 3_900_000
MAX_PROTO_INT64 = (1 << 63) - 1
HTTP_STATUS_MIN = 100
HTTP_STATUS_MAX = 599
MAX_DOMAIN_LENGTH = 253
ProtoCount = Annotated[int, Field(ge=0, le=MAX_PROTO_INT64)]


class DirectSpiderDomainStats(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    domain: str = Field(min_length=1, max_length=MAX_DOMAIN_LENGTH)
    reqs: ProtoCount = 0
    successRate: float = Field(default=0, ge=0, le=100)
    latency: float = Field(default=0, ge=0)


class DirectSpiderStats(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    request_count: ProtoCount = 0
    response_count: ProtoCount = 0
    item_scraped_count: ProtoCount = 0
    error_count: ProtoCount = 0
    avg_latency_ms: float = Field(default=0, ge=0)
    requests_per_minute: float = Field(default=0, ge=0)
    status_codes: dict[str, ProtoCount] = Field(default_factory=dict)
    domain_stats: list[DirectSpiderDomainStats] = Field(default_factory=list)

    @field_validator("status_codes")
    @classmethod
    def validate_status_codes(cls, value: dict[str, int]) -> dict[str, int]:
        normalized = {}
        for raw_code, count in value.items():
            try:
                code = int(raw_code)
            except ValueError as exc:
                raise ValueError(f"invalid HTTP status code: {raw_code}") from exc
            if code < HTTP_STATUS_MIN or code > HTTP_STATUS_MAX:
                raise ValueError("HTTP status code must be between 100 and 599")
            key = str(code)
            if key in normalized:
                raise ValueError(f"duplicate HTTP status code: {code}")
            normalized[key] = count
        return normalized


class DirectLeaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    cpu: float | None = Field(default=None, ge=0, le=100)
    memory: float | None = Field(default=None, ge=0, le=100)
    disk: float | None = Field(default=None, ge=0, le=100)
    running_tasks: ProtoCount | None = None
    max_concurrent_tasks: ProtoCount | None = Field(default=None, ge=1)
    task_count: ProtoCount | None = None
    project_count: ProtoCount | None = None
    env_count: ProtoCount | None = None
    spider_stats: DirectSpiderStats | None = None
    # 下面这批必须与 contracts/proto/common.proto 的 ResourceMetrics 11-19 号字段
    # 保持一致：两种传输模式的 Worker 共用同一个 heartbeat_metrics_dict()，
    # 而本模型是 extra="forbid"。少一个字段，Direct Worker 的每一次心跳续租都会
    # 被判 422 —— 租约永不续期 → 失租回收 → 容器无限重启（真实环境实测）。
    memory_total_bytes: ProtoCount | None = None
    memory_used_bytes: ProtoCount | None = None
    memory_available_bytes: ProtoCount | None = None
    disk_total_bytes: ProtoCount | None = None
    disk_used_bytes: ProtoCount | None = None
    disk_free_bytes: ProtoCount | None = None
    cpu_cores: ProtoCount | None = None
    uptime_seconds: ProtoCount | None = None
    queued_tasks: ProtoCount | None = None
    # 20-21 号字段：Worker 上报的**生效**单任务限额。0 是合法值（没有限额在生效），
    # 所以不设 ge=1 —— 把 0 判非法会让整个心跳 422，重演上面那条重启环。
    task_memory_limit_mb: ProtoCount | None = None
    task_cpu_time_limit_sec: ProtoCount | None = None


class DirectLeaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    operation: Literal["lease"]
    current_lease_id: str = Field(default="", max_length=MAX_LEASE_ID_LENGTH)
    metrics: DirectLeaseMetrics | None = None
    # 与 Gateway ``LeaseRequest.capabilities`` 同构的 JSON 编码能力快照，且同样
    # 必填：控制面不得改用 ``workers.capabilities`` 代填。那一列由心跳链路异步
    # 写入，首次签发时还是空的，等心跳落库后的下一次续租就会被 GRANT_LUA 判
    # ``capabilities_changed`` → Lease 撤销 → Worker 执行任务中途自我停机。
    capabilities: dict[str, str]


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
    "DirectLeaseMetrics",
    "DirectLeaseRequest",
    "DirectSpiderDomainStats",
    "DirectSpiderStats",
    "DirectOwnershipClaimRequest",
    "DirectOwnershipIdentityRequest",
    "DirectOwnershipReleaseRequest",
    "DirectOwnershipRenewRequest",
    "DirectSpiderItemsRequest",
    "DirectSpiderMetaRequest",
]
