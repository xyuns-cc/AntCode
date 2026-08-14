"""Backend-specific helpers for transport contract fixtures."""

from __future__ import annotations

import os
import secrets
import socket
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import pytest
from antcode_worker.transport.redis.direct_control import DirectLeaseGrant

from tests.contracts.fake_gateway import RunningFakeGateway

REDIS_TEST_URL = os.environ.get(
    "ANTCODE_CONTRACT_REDIS_URL",
    "redis://localhost:16379/14",
)
_CONTRACT_TASK_PAYLOAD_SECRET = "contract-task-payload-secret-material-0001"


def _redis_endpoint() -> tuple[str, int]:
    parsed = urlparse(REDIS_TEST_URL)
    return parsed.hostname or "localhost", parsed.port or 6379


REDIS_TEST_HOST, REDIS_TEST_PORT = _redis_endpoint()


class _InProcessDirectControl:
    """Exercise Direct control semantics without bypassing production fences."""

    def __init__(self, redis: Any, *, worker_id: str, namespace: str) -> None:
        from antcode_core.application.services.lease_service import LeaseStore

        self._redis = redis
        self._worker_id = worker_id
        self._namespace = namespace
        self._leases = LeaseStore(redis, namespace=namespace)

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None,
        capabilities: dict[str, str],
    ) -> DirectLeaseGrant:
        """与真实 ``DirectControlClient`` 同构：返回带权威 ``ttl_ms`` 的应答。

        撤销分支同样带上 policy 的 ttl/节拍——Worker 侧对两条分支用同一套
        ``renew_after_ms * 2 < ttl_ms`` 不变量校验，替身少带一个字段就会让
        续期窗口构造失败，而那是替身的问题不是被测行为。
        """
        from antcode_contracts.capabilities import decode_capabilities
        from antcode_core.application.services.lease_service import LeaseRevokedError

        policy = self._leases.policy
        try:
            lease = await self._leases.grant(
                self._worker_id,
                current_lease_id=current_lease_id,
                metrics=metrics,
                capabilities=decode_capabilities(capabilities),
            )
        except LeaseRevokedError:
            return DirectLeaseGrant(
                lease_id="",
                expires_at_ms=0,
                renew_after_ms=policy.renew_after_ms,
                ttl_ms=policy.ttl_ms,
                revoked=True,
            )
        return DirectLeaseGrant(
            lease_id=lease.lease_id,
            expires_at_ms=lease.expires_at_ms,
            renew_after_ms=policy.renew_after_ms,
            ttl_ms=policy.ttl_ms,
            revoked=False,
        )

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
        from antcode_core.application.services.workers.run_ownership_fence import (
            OwnershipOutcome,
            renew_run_ownership,
        )

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

    async def deregister(self, lease_id: str, reason: str) -> bool:
        return await self._leases.revoke(self._worker_id, reason=reason, lease_id=lease_id)

    async def aclose(self) -> None:
        await self._redis.aclose()


def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def make_redis_transport(ids: Any) -> Any:
    host, port = _redis_endpoint()
    if not _tcp_reachable(host, port):
        pytest.fail(f"Redis on {host}:{port} is not reachable")

    import redis.asyncio as aioredis
    from antcode_worker.transport.redis import RedisTransport
    from antcode_worker.transport.redis.keys import RedisKeys

    namespace = f"antcode-test-{secrets.token_hex(4)}"
    keys = RedisKeys(namespace=namespace)
    control_redis = aioredis.from_url(REDIS_TEST_URL, decode_responses=False)
    transport = RedisTransport(
        redis_url=REDIS_TEST_URL,
        worker_id=ids.worker_id,
        namespace=namespace,
        consumer_group=keys.consumer_group_name(),
        direct_control=_InProcessDirectControl(
            control_redis,
            worker_id=ids.worker_id,
            namespace=namespace,
        ),
        task_payload_secret=_CONTRACT_TASK_PAYLOAD_SECRET,
    )
    transport._test_namespace = namespace  # type: ignore[attr-defined]
    transport._test_keys = keys  # type: ignore[attr-defined]
    return transport


async def make_gateway_transport(ids: Any, gateway: RunningFakeGateway) -> Any:
    from antcode_worker.transport.gateway import GatewayConfig, GatewayTransport

    config = GatewayConfig(
        gateway_host=gateway.host,
        gateway_port=gateway.port,
        use_tls=False,
        auth_method="api_key",
        api_key="contract-test-api-key",
        worker_id=ids.worker_id,
        task_payload_secret=gateway.state.task_payload_secret,
        connect_timeout=2.0,
        call_timeout=2.0,
        enable_reconnect=False,
    )
    transport = GatewayTransport(gateway_config=config)
    transport._test_gateway_state = gateway.state  # type: ignore[attr-defined]
    return transport


async def _delete_matching_keys(client: Any, pattern: str) -> None:
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=pattern, count=500)
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            return


async def cleanup_redis_transport(transport: Any) -> None:
    namespace = getattr(transport, "_test_namespace", None)
    if not namespace:
        return
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await _delete_matching_keys(client, f"{namespace}:*")
        await _delete_matching_keys(client, f"{{{namespace}}}:*")
    finally:
        await client.aclose()


@asynccontextmanager
async def redis_client(decode_responses: bool = True):
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        REDIS_TEST_URL,
        decode_responses=decode_responses,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def produce_redis_task(transport: Any, payload: dict[str, Any]) -> str:
    from antcode_core.common.security.task_payload_envelope import seal_ready_payload

    keys = getattr(transport, "_test_keys", None)
    assert keys is not None, "redis transport missing test keys"
    lease_store = getattr(transport, "_lease_store", None)
    assert lease_store is not None, "redis transport missing lease store"
    lease = await lease_store.get(transport._worker_id, include_expired=False)
    assert lease is not None, "redis transport missing live lease"
    assert lease.lease_id == transport._lease_id, "redis transport lease generation changed"
    assert lease.sequence > 0, "redis transport lease sequence is invalid"
    if "dispatch_lease_id" in payload or "dispatch_lease_gen" in payload:
        raise ValueError("contract producer assigns the dispatch lease fence")
    fenced_payload = seal_ready_payload(
        {
            **payload,
            "dispatch_lease_id": lease.lease_id,
            "dispatch_lease_gen": lease.sequence,
        },
        worker_id=transport._worker_id,
        worker_secret=transport._task_payload_secret,
    )
    stream = keys.task_ready_stream(transport._worker_id)
    async with redis_client() as client:
        return await client.xadd(stream, {key: str(value) for key, value in fenced_payload.items()})
