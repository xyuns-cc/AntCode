"""Default Redis lifecycle wiring for the Worker service."""


async def disable_worker_lease(worker_id: str, reason: str) -> bool:
    return await (await _worker_lease_lifecycle_fence()).disable(worker_id, reason)


async def enable_worker_lease(worker_id: str, expected_reasons: tuple[str, ...]) -> bool:
    return await (await _worker_lease_lifecycle_fence()).enable(worker_id, expected_reasons=expected_reasons)


async def _worker_lease_lifecycle_fence():
    from antcode_core.application.services.lease_service import LeaseStore
    from antcode_core.application.services.workers.worker_lease_lifecycle import WorkerLeaseLifecycleFence
    from antcode_core.infrastructure.redis import get_redis_client, redis_namespace

    redis = await get_redis_client()
    if redis is None:
        raise RuntimeError("Redis client unavailable for Worker lease lifecycle")
    return WorkerLeaseLifecycleFence(redis, LeaseStore(redis, namespace=redis_namespace()))


__all__ = ["disable_worker_lease", "enable_worker_lease"]
