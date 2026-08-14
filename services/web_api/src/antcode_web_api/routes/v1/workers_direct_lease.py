"""Direct Worker Lease issuance and revocation."""

from __future__ import annotations

from typing import Any

from antcode_contracts.capabilities import decode_capabilities
from antcode_contracts.wire_contract import WorkerWireContractError
from antcode_core.application.services.lease_service import (
    LeaseConflictError,
    LeaseIneligibleError,
    LeaseRevokedError,
    LeaseStore,
    wire_lease_policy,
)
from antcode_core.application.services.workers.worker_lease_authority import (
    get_worker_lease_eligibility,
)
from antcode_core.application.services.workers.worker_lease_issuance import (
    WorkerLeaseGrantRequest,
    WorkerLeaseRejected,
    grant_authorized_worker_lease,
)
from antcode_core.infrastructure.redis import worker_heartbeat_key
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from fastapi import HTTPException, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1.workers_direct_models import DirectDeregisterRequest, DirectLeaseRequest


async def grant_direct_lease(worker: Any, request: DirectLeaseRequest, redis: Any) -> BaseResponse:
    worker_id = str(worker.public_id)
    store = LeaseStore(redis, namespace=redis_namespace(), policy=wire_lease_policy())
    metrics = request.metrics.model_dump(exclude_none=True) if request.metrics else None
    capabilities = _decoded_worker_capabilities(request)
    try:
        lease = await grant_authorized_worker_lease(
            store,
            get_worker_lease_eligibility,
            WorkerLeaseGrantRequest(
                worker_id=worker_id,
                current_lease_id=request.current_lease_id,
                metrics=metrics,
                capabilities=capabilities,
            ),
        )
    except WorkerWireContractError as exc:
        # 与 Gateway 的拒发同义：错配的 Worker 拿不到 Lease，也就写不出心跳、
        # 进不了派发候选。426 让"升级 Worker"这个处置直接写在状态码上。
        logger.error("Direct Lease 拒发（Worker 线协议版本不兼容）: worker_id={} reason={}", worker_id, exc)
        raise HTTPException(status_code=status.HTTP_426_UPGRADE_REQUIRED, detail=str(exc)) from exc
    except WorkerLeaseRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LeaseIneligibleError, LeaseRevokedError):
        return _revoked_response(store)
    except LeaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Direct Lease grant 失败: worker_id={}", worker_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lease persistence unavailable"
        ) from exc
    await _project_worker_capabilities(worker, capabilities)
    return success(
        {
            "lease_id": lease.lease_id,
            "expires_at_ms": lease.expires_at_ms,
            "renew_after_ms": store.policy.renew_after_ms,
            # 权威 TTL 必须随每次签发下发：Worker 用它校验
            # renew_after_ms * 2 < ttl_ms，缺了就只能假定本地默认值。
            "ttl_ms": store.policy.ttl_ms,
            "revoked": False,
        }
    )


def _decoded_worker_capabilities(request: DirectLeaseRequest) -> dict[str, Any]:
    """Decode the Worker-supplied capability snapshot; malformed input is a client error."""
    try:
        return decode_capabilities(request.capabilities)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _project_worker_capabilities(worker: Any, capabilities: dict[str, Any]) -> None:
    """Mirror the authoritative Lease snapshot into the PostgreSQL routing projection.

    ``worker_capability_routing`` 对 Direct Worker 要求 ``workers.capabilities``
    与当前 Lease 的 ``capabilities_json`` 逐字节一致，否则派发前直接拒单。心跳
    链路写这一列是异步的，首个 Lease 到首次心跳落库之间会出现空快照窗口，
    因此由签发路径同步写入同一份权威快照。
    """
    if worker.capabilities == capabilities:
        return
    worker.capabilities = capabilities
    await worker.save(update_fields=["capabilities"])


async def deregister_direct_worker(worker_id: str, request: DirectDeregisterRequest, redis: Any) -> BaseResponse:
    store = LeaseStore(redis, namespace=redis_namespace(), policy=wire_lease_policy())
    try:
        revoked = await store.revoke(worker_id, reason=request.reason, lease_id=request.lease_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lease persistence unavailable"
        ) from exc
    heartbeat_deleted = await _delete_revoked_heartbeat(redis, worker_id) if revoked else False
    return success({"revoked": revoked, "heartbeat_deleted": heartbeat_deleted})


async def _delete_revoked_heartbeat(redis: Any, worker_id: str) -> bool:
    try:
        key = worker_heartbeat_key(worker_id, namespace=redis_namespace())
        return bool(await redis.delete(key))
    except Exception:
        logger.exception("Direct deregister 已撤销 Lease，但清理 heartbeat 失败: worker_id={}", worker_id)
        return False


def _revoked_response(store: LeaseStore) -> BaseResponse:
    return success(
        {
            "lease_id": "",
            "expires_at_ms": 0,
            "renew_after_ms": store.policy.renew_after_ms,
            "ttl_ms": store.policy.ttl_ms,
            "revoked": True,
            "revoke_reason": "lease generation revoked",
        }
    )


__all__ = ["deregister_direct_worker", "grant_direct_lease"]
