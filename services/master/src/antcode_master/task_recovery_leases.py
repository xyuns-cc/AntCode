"""Authoritative Lease generations used by interrupted-run recovery."""

from __future__ import annotations

import asyncio

from antcode_core.application.services.lease_service import Lease, LeaseStore, wire_lease_policy
from antcode_core.infrastructure.redis import get_redis_client, redis_namespace
from loguru import logger

LEASE_READ_BATCH_SIZE = 100


async def load_active_lease_ids() -> dict[str, str] | None:
    """Return current ``worker_id -> lease_id`` or ``None`` when Redis is unavailable."""
    try:
        redis = await get_redis_client()
        if redis is None:
            logger.warning("Lease store 不可达(Redis client None),跳过本轮判死")
            return None
        store = LeaseStore(redis, namespace=redis_namespace(), policy=wire_lease_policy())
        worker_ids = await store.list_active()
        leases = await _load_leases(store, worker_ids)
        return {lease.worker_id: lease.lease_id for lease in leases if lease is not None}
    except Exception as exc:
        logger.warning("读取 active lease generations 失败(保守跳过判死): {}", exc)
        return None


async def _load_leases(store: LeaseStore, worker_ids: list[str]) -> list[Lease | None]:
    leases: list[Lease | None] = []
    for offset in range(0, len(worker_ids), LEASE_READ_BATCH_SIZE):
        batch = worker_ids[offset : offset + LEASE_READ_BATCH_SIZE]
        leases.extend(await asyncio.gather(*(store.get(worker_id, include_expired=False) for worker_id in batch)))
    return leases


__all__ = ["load_active_lease_ids"]
