"""Field-level contract for Gateway run-ownership RPCs."""

from dataclasses import dataclass
from typing import Any

import grpc

MAX_RUN_OWNERSHIP_TTL_MS = 3_900_000
MAX_RUN_ID_LENGTH = 64
MAX_LEASE_ID_LENGTH = 64


@dataclass(frozen=True)
class RunOwnershipIdentity:
    worker_id: str
    lease_id: str
    run_id: str
    ttl_ms: int | None


class OwnershipBindError(RuntimeError):
    def __init__(self, code: grpc.StatusCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


async def validate_ownership_fields(
    request: Any,
    context: grpc.aio.ServicerContext,
    worker_id: str,
    *,
    require_ttl: bool,
) -> RunOwnershipIdentity | None:
    run_id = str(request.run_id or "")
    lease_id = str(request.lease_id or "")
    ttl_ms = int(request.ttl_ms) if require_ttl else None
    invalid = (
        not run_id
        or run_id != run_id.strip()
        or len(run_id) > MAX_RUN_ID_LENGTH
        or not lease_id
        or lease_id != lease_id.strip()
        or len(lease_id) > MAX_LEASE_ID_LENGTH
        or (require_ttl and (ttl_ms is None or ttl_ms <= 0 or ttl_ms > MAX_RUN_OWNERSHIP_TTL_MS))
    )
    if invalid:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "run ownership 请求字段无效")
        return None
    return RunOwnershipIdentity(worker_id, lease_id, run_id, ttl_ms)


__all__ = [
    "MAX_RUN_OWNERSHIP_TTL_MS",
    "OwnershipBindError",
    "RunOwnershipIdentity",
    "validate_ownership_fields",
]
