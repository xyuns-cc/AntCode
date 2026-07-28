"""Route registration for the Direct Worker trusted control plane."""

from fastapi import Depends

from antcode_web_api.routes.v1.workers_direct_control import (
    DirectDeregisterRequest,
    DirectLeaseRequest,
    DirectOwnershipClaimRequest,
    DirectOwnershipReleaseRequest,
    DirectOwnershipRenewRequest,
    DirectSpiderItemsRequest,
    DirectSpiderMetaRequest,
    _authenticated_worker,
    _claim_direct_ownership,
    _deregister_direct_worker,
    _grant_direct_lease,
    _release_direct_ownership,
    _renew_direct_ownership,
)
from antcode_web_api.routes.v1.workers_direct_logs import DirectLogBatchRequest, ingest_direct_log_batch
from antcode_web_api.routes.v1.workers_direct_spider import ingest_direct_spider_items, ingest_direct_spider_meta


def register_direct_control_routes(router, verify_worker_credential_headers) -> None:
    @router.post("/{worker_id}/direct-control/lease")
    async def renew_direct_lease(
        worker_id: str,
        request: DirectLeaseRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        _authenticated_worker(worker_id, auth_context)
        return await _grant_direct_lease(worker_id, request)

    @router.post("/{worker_id}/direct-control/run-ownership/claim")
    async def claim_direct_run_ownership(
        worker_id: str,
        request: DirectOwnershipClaimRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        worker = _authenticated_worker(worker_id, auth_context)
        return await _claim_direct_ownership(worker, request)

    @router.post("/{worker_id}/direct-control/run-ownership/renew")
    async def renew_direct_run_ownership(
        worker_id: str,
        request: DirectOwnershipRenewRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        worker = _authenticated_worker(worker_id, auth_context)
        return await _renew_direct_ownership(worker, request)

    @router.post("/{worker_id}/direct-control/run-ownership/release")
    async def release_direct_run_ownership(
        worker_id: str,
        request: DirectOwnershipReleaseRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        worker = _authenticated_worker(worker_id, auth_context)
        return await _release_direct_ownership(worker, request)

    @router.post("/{worker_id}/direct-control/deregister")
    async def deregister_direct_worker(
        worker_id: str,
        request: DirectDeregisterRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        _authenticated_worker(worker_id, auth_context)
        return await _deregister_direct_worker(worker_id, request)

    @router.post("/{worker_id}/direct-control/spider-items")
    async def write_direct_spider_items(
        worker_id: str,
        request: DirectSpiderItemsRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        worker = _authenticated_worker(worker_id, auth_context)
        return await ingest_direct_spider_items(worker, request)

    @router.post("/{worker_id}/direct-control/spider-meta")
    async def write_direct_spider_meta(
        worker_id: str,
        request: DirectSpiderMetaRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        worker = _authenticated_worker(worker_id, auth_context)
        return await ingest_direct_spider_meta(worker, request)

    @router.post("/{worker_id}/direct-control/logs")
    async def write_direct_logs(
        worker_id: str,
        request: DirectLogBatchRequest,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        worker = _authenticated_worker(worker_id, auth_context)
        return await ingest_direct_log_batch(worker, request)


__all__ = ["register_direct_control_routes"]
