"""Worker integration test helpers."""

from typing import Any, cast

import pytest
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError

_ORIGINAL_FROM_URL = aioredis.from_url


class _InProcessLeaseControl:
    """Use the real Redis lease state machine as the integration control plane."""

    def __init__(self, redis_client: Any, *, worker_id: str, namespace: str) -> None:
        from antcode_core.application.services.lease_service import LeaseStore

        self._redis = redis_client
        self._worker_id = worker_id
        self._namespace = namespace
        self._leases = LeaseStore(redis_client, namespace=namespace)

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None,
    ) -> tuple[str, int, int, bool]:
        lease = await self._leases.grant(
            self._worker_id,
            current_lease_id=current_lease_id,
            metrics=metrics,
        )
        return lease.lease_id, lease.expires_at_ms, self._leases.policy.renew_after_ms, False

    async def deregister(self, lease_id: str, reason: str) -> bool:
        return await self._leases.revoke(self._worker_id, reason=reason, lease_id=lease_id)

    async def claim_run_ownership(self, lease_id: str, run_id: str, ttl_ms: int) -> bool:
        from antcode_core.application.services.workers.run_ownership_fence import (
            OwnershipOutcome,
            claim_run_ownership,
        )

        outcome = await claim_run_ownership(
            self._redis,
            worker_id=self._worker_id,
            lease_id=lease_id,
            run_id=run_id,
            ttl_ms=ttl_ms,
            namespace=self._namespace,
        )
        return outcome is OwnershipOutcome.ACQUIRED

    async def renew_run_ownership(self, lease_id: str, run_id: str, ttl_ms: int) -> bool:
        from antcode_core.application.services.workers.run_ownership_fence import OwnershipOutcome, renew_run_ownership

        outcome = await renew_run_ownership(
            self._redis,
            worker_id=self._worker_id,
            lease_id=lease_id,
            run_id=run_id,
            ttl_ms=ttl_ms,
            namespace=self._namespace,
        )
        return outcome is OwnershipOutcome.ACQUIRED

    async def release_run_ownership(self, lease_id: str, run_id: str) -> bool:
        from antcode_core.application.services.workers.run_ownership_fence import release_run_ownership

        return await release_run_ownership(
            self._redis,
            worker_id=self._worker_id,
            lease_id=lease_id,
            run_id=run_id,
            namespace=self._namespace,
        )

    async def report_log_batch(self, payload: bytes) -> bool:
        from antcode_contracts import data_pb2
        from antcode_core.application.services.workers.log_batch_validation import validate_log_batch
        from antcode_core.application.services.workers.log_ingest_fence import append_fenced_log_batch
        from antcode_core.common.log_limits import LogBatchLimits

        batch = data_pb2.LogBatch.FromString(payload)
        validate_log_batch(batch, limits=LogBatchLimits())
        await append_fenced_log_batch(
            self._redis,
            payload,
            worker_id=batch.worker_id,
            lease_id=batch.lease_id,
            run_ids={entry.run_id for entry in batch.entries},
            namespace=self._namespace,
        )
        return True

    async def aclose(self) -> None:
        await self._redis.aclose()


def _patched_from_url(url, *args, **kwargs):
    kwargs.setdefault("retry_on_timeout", True)
    kwargs.setdefault(
        "retry",
        Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=5),
    )
    kwargs.setdefault(
        "retry_on_error",
        [ConnectionError, TimeoutError],
    )
    kwargs.setdefault("socket_timeout", 30)
    kwargs.setdefault("socket_connect_timeout", 10)
    kwargs.setdefault("socket_keepalive", True)
    kwargs.setdefault("health_check_interval", 30)
    kwargs.setdefault("max_connections", 200)
    return _ORIGINAL_FROM_URL(url, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _patch_redis_from_url():
    aioredis.from_url = _patched_from_url
    yield
    aioredis.from_url = _ORIGINAL_FROM_URL


@pytest.fixture
def direct_transport_factory():
    """Construct a Direct transport backed by a real in-process lease control."""
    from antcode_worker.transport import RedisTransport
    from antcode_worker.transport.redis.direct_control import DirectControlClient
    from antcode_worker.transport.redis.keys import RedisKeys

    def build(
        redis_url: str,
        worker_id: str,
        config: Any = None,
        *,
        namespace: str | None = None,
    ) -> RedisTransport:
        namespace = RedisKeys(namespace=namespace).namespace
        control_redis = aioredis.from_url(redis_url, decode_responses=True)
        control = cast(
            DirectControlClient, _InProcessLeaseControl(control_redis, worker_id=worker_id, namespace=namespace)
        )
        return RedisTransport(
            redis_url=redis_url,
            worker_id=worker_id,
            config=config,
            namespace=namespace,
            direct_control=control,
        )

    return build
