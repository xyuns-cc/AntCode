"""Trusted control-plane operations for Direct Workers."""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.lease_service import LeaseStore, wire_lease_policy
from antcode_core.application.services.workers.log_ingest_generation import (
    read_log_ingest_cutoff,
)
from antcode_core.application.services.workers.run_ownership_fence import (
    OwnershipOutcome,
    claim_run_ownership,
    release_run_ownership,
    renew_run_ownership,
)
from antcode_core.application.services.workers.run_ownership_service import (
    bind_worker_run_lease_generation,
    require_worker_owns_runs_for_lease,
)
from antcode_core.infrastructure.redis import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from fastapi import HTTPException, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1.workers_direct_lease import deregister_direct_worker, grant_direct_lease
from antcode_web_api.routes.v1.workers_direct_models import (
    DirectDeregisterRequest,
    DirectLeaseRequest,
    DirectOwnershipClaimRequest,
    DirectOwnershipIdentityRequest,
    DirectOwnershipReleaseRequest,
    DirectOwnershipRenewRequest,
    DirectSpiderItemsRequest,
    DirectSpiderMetaRequest,
)


def _authenticated_worker(worker_id: str, auth_context: dict[str, Any]) -> Any:
    worker = auth_context.get("worker")
    authenticated_id = str(getattr(worker, "public_id", "") or "")
    if authenticated_id != worker_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker 身份不匹配")
    if str(getattr(worker, "transport_mode", "") or "").lower() != "direct":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅 Direct Worker 可调用此接口")
    return worker


async def _redis_client() -> Any:
    redis = await get_redis_client()
    if redis is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")
    return redis


async def _grant_direct_lease(worker: Any, request: DirectLeaseRequest) -> BaseResponse:
    redis = await _redis_client()
    return await grant_direct_lease(worker, request, redis)


async def _release_failed_claim(redis: Any, request: DirectOwnershipClaimRequest, worker_id: str) -> bool:
    try:
        return await release_run_ownership(
            redis,
            worker_id=worker_id,
            lease_id=request.lease_id,
            run_id=request.run_id,
            namespace=redis_namespace(),
        )
    except Exception:
        logger.exception("Direct ownership bind 失败后的 release 也失败: run_id={}", request.run_id)
        return False


async def _bind_claimed_run(redis: Any, request: DirectOwnershipClaimRequest, worker_id: str) -> None:
    store = LeaseStore(redis, namespace=redis_namespace(), policy=wire_lease_policy())
    lease = await store.get(worker_id, include_expired=False)
    current = lease is not None and lease.lease_id == request.lease_id
    if current:
        current = await store.is_current(worker_id, request.lease_id)
    if not current or lease is None:
        raise PermissionError("lease 在 ownership claim 后已失效或换代")
    generation = lease.sequence if lease.sequence > 0 else lease.granted_at_ms
    log_cutoff_id = await read_log_ingest_cutoff(redis, namespace=redis_namespace())
    await bind_worker_run_lease_generation(
        worker_id,
        request.run_id,
        lease_id=request.lease_id,
        lease_gen=generation,
        log_cutoff_id=log_cutoff_id,
    )
    outcome = await renew_run_ownership(
        redis,
        worker_id=worker_id,
        lease_id=request.lease_id,
        run_id=request.run_id,
        ttl_ms=request.ttl_ms,
        namespace=redis_namespace(),
    )
    if outcome is not OwnershipOutcome.ACQUIRED:
        raise PermissionError("lease 在 ownership bind 后已失效或换代")


async def _require_claim_preconditions(worker: Any, request: DirectOwnershipClaimRequest, redis: Any) -> str:
    worker_id = str(worker.public_id)
    store = LeaseStore(redis, namespace=redis_namespace(), policy=wire_lease_policy())
    try:
        current = await store.is_current(worker_id, request.lease_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lease persistence unavailable"
        ) from exc
    if not current:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="lease_id 不是当前 Worker Lease")
    try:
        await require_worker_owns_runs_for_lease(
            worker,
            [request.run_id],
            lease_id=request.lease_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="TaskRun lookup unavailable"
        ) from exc
    return worker_id


async def _run_claim_fence(redis: Any, request: DirectOwnershipClaimRequest, worker_id: str) -> OwnershipOutcome:
    try:
        return await claim_run_ownership(
            redis,
            worker_id=worker_id,
            lease_id=request.lease_id,
            run_id=request.run_id,
            ttl_ms=request.ttl_ms,
            namespace=redis_namespace(),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run ownership persistence unavailable",
        ) from exc


async def _claim_direct_ownership(worker: Any, request: DirectOwnershipClaimRequest) -> BaseResponse:
    redis = await _redis_client()
    worker_id = await _require_claim_preconditions(worker, request, redis)
    outcome = await _run_claim_fence(redis, request, worker_id)
    if outcome is OwnershipOutcome.LEASE_STALE:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="lease generation superseded")
    if outcome is OwnershipOutcome.HELD_BY_OTHER:
        return success({"acquired": False})
    try:
        await _bind_claimed_run(redis, request, worker_id)
    except Exception as exc:
        compensated = await _release_failed_claim(redis, request, worker_id)
        if not compensated:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="run ownership bind 与补偿释放均失败",
            ) from exc
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(exc)) from exc
        logger.exception("Direct ownership PG bind 失败: run_id={}", request.run_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run ownership binding unavailable",
        ) from exc
    return success({"acquired": True})


async def _require_bound_run(worker: Any, request: DirectOwnershipIdentityRequest) -> tuple[Any, str]:
    worker_id = str(worker.public_id)
    try:
        await require_worker_owns_runs_for_lease(worker, [request.run_id], lease_id=request.lease_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="TaskRun lookup unavailable"
        ) from exc
    return await _redis_client(), worker_id


async def _renew_direct_ownership(worker: Any, request: DirectOwnershipRenewRequest) -> BaseResponse:
    redis, worker_id = await _require_bound_run(worker, request)
    try:
        outcome = await renew_run_ownership(
            redis,
            worker_id=worker_id,
            lease_id=request.lease_id,
            run_id=request.run_id,
            ttl_ms=request.ttl_ms,
            namespace=redis_namespace(),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run ownership persistence unavailable",
        ) from exc
    if outcome is OwnershipOutcome.LEASE_STALE:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="lease generation superseded")
    return success({"renewed": outcome is OwnershipOutcome.ACQUIRED})


async def _release_direct_ownership(worker: Any, request: DirectOwnershipReleaseRequest) -> BaseResponse:
    # release 只清理已认证 Worker 请求 token 精确匹配的 ownership。TaskRun
    # 可能已结算删除，Lease 也可能已换代；两者都不应阻止旧 token 清理。
    redis = await _redis_client()
    worker_id = str(worker.public_id)
    try:
        released = await release_run_ownership(
            redis,
            worker_id=worker_id,
            lease_id=request.lease_id,
            run_id=request.run_id,
            namespace=redis_namespace(),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run ownership persistence unavailable",
        ) from exc
    return success({"released": released})


async def _deregister_direct_worker(worker_id: str, request: DirectDeregisterRequest) -> BaseResponse:
    redis = await _redis_client()
    return await deregister_direct_worker(worker_id, request, redis)


__all__ = [
    "DirectLeaseRequest",
    "DirectOwnershipIdentityRequest",
    "DirectOwnershipClaimRequest",
    "DirectOwnershipRenewRequest",
    "DirectOwnershipReleaseRequest",
    "DirectSpiderItemsRequest",
    "DirectSpiderMetaRequest",
]
