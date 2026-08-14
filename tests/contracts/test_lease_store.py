"""Contract tests for ``antcode_core.application.services.lease_service.LeaseStore``.

These run against the same Redis container used by the rest of
``tests/contracts/`` (see ``docker-compose.test.yml``).  They cover:

- first-time grant produces a fresh ``lease_id`` and marks the worker active
- renewal with the matching ``current_lease_id`` keeps the ``lease_id`` stable
  but pushes ``expires_at_ms`` forward
- an empty or mismatched ``current_lease_id`` cannot replace an active lease
- an expired lease can be replaced with a fresh generation
- ``revoke`` removes the active marker
- ``sweep_expired`` removes leases past their TTL and reports the evicted ids

Redis is a required contract dependency. An unreachable server fails the suite
instead of silently reducing coverage.
"""

from __future__ import annotations

import asyncio
import secrets

import pytest
import pytest_asyncio
from antcode_core.application.services.lease_service import (
    Lease,
    LeaseConflictError,
    LeaseIneligibleError,
    LeaseStore,
)

from tests.contracts.lease_fixtures import lease_store_fixture, redis_client_fixture

pytestmark = pytest.mark.asyncio


redis_client = pytest_asyncio.fixture(redis_client_fixture)
lease_store = pytest_asyncio.fixture(lease_store_fixture)


async def test_grant_first_time_marks_active(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"

    lease = await lease_store.grant(worker_id, current_lease_id="")

    assert isinstance(lease, Lease)
    assert lease.worker_id == worker_id
    assert lease.lease_id  # non-empty
    assert lease.expires_at_ms > lease.granted_at_ms
    assert await lease_store.is_active(worker_id) is True

    # ZSet should also carry this worker at score == expires_at_ms.
    expiring_key = f"{{{lease_store.namespace}}}:lease:expiring"
    score = await redis_client.zscore(expiring_key, worker_id)
    assert score is not None
    assert int(score) == lease.expires_at_ms


async def test_grant_renew_with_matching_lease_id_keeps_id_and_pushes_expiry(
    lease_store: LeaseStore,
):
    worker_id = f"worker-{secrets.token_hex(3)}"

    first = await lease_store.grant(worker_id, current_lease_id="")
    # Sleep slightly so granted_at_ms is provably later.
    await asyncio.sleep(0.02)
    renewed = await lease_store.grant(worker_id, current_lease_id=first.lease_id)

    assert renewed.lease_id == first.lease_id, "续租应保留同一 lease_id"
    assert renewed.expires_at_ms >= first.expires_at_ms
    assert renewed.granted_at_ms >= first.granted_at_ms


@pytest.mark.parametrize("current_lease_id", ["", "stale-lease-id"])
async def test_grant_rejects_claimant_that_does_not_own_active_lease(
    lease_store: LeaseStore,
    redis_client,
    current_lease_id: str,
):
    worker_id = f"worker-{secrets.token_hex(3)}"

    first = await lease_store.grant(worker_id, current_lease_id="")
    lease_key = f"{{{lease_store.namespace}}}:lease:data:{worker_id}"
    expiring_key = f"{{{lease_store.namespace}}}:lease:expiring"
    with pytest.raises(LeaseConflictError) as caught:
        await lease_store.grant(
            worker_id,
            current_lease_id=current_lease_id,
            metrics={"claimant": "must-not-persist"},
        )

    assert caught.value.worker_id == worker_id
    assert caught.value.current_lease_id == current_lease_id
    assert await lease_store.get(worker_id) == first
    assert await redis_client.hget(lease_key, "metrics_json") is None
    assert int(await redis_client.zscore(expiring_key, worker_id)) == first.expires_at_ms
    assert await lease_store.is_active(worker_id) is True


async def test_concurrent_first_grants_have_exactly_one_winner(lease_store: LeaseStore):
    worker_id = f"worker-{secrets.token_hex(3)}"

    results = await asyncio.gather(
        lease_store.grant(worker_id, current_lease_id=""),
        lease_store.grant(worker_id, current_lease_id=""),
        return_exceptions=True,
    )

    leases = [result for result in results if isinstance(result, Lease)]
    conflicts = [result for result in results if isinstance(result, LeaseConflictError)]
    assert len(leases) == 1
    assert len(conflicts) == 1
    assert await lease_store.get(worker_id) == leases[0]


async def test_grant_replaces_expired_lease(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"
    first = await lease_store.grant(worker_id, current_lease_id="")
    lease_key = f"{{{lease_store.namespace}}}:lease:data:{worker_id}"
    await redis_client.hset(lease_key, "expires_at_ms", first.granted_at_ms - 1)
    await asyncio.sleep(0.002)

    replacement = await lease_store.grant(worker_id, current_lease_id="")

    assert replacement.lease_id != first.lease_id
    assert replacement.expires_at_ms > first.expires_at_ms


async def test_revoke_clears_active_set(lease_store: LeaseStore):
    worker_id = f"worker-{secrets.token_hex(3)}"

    await lease_store.grant(worker_id, current_lease_id="")
    assert await lease_store.is_active(worker_id) is True

    revoked = await lease_store.revoke(worker_id, reason="deregister")
    assert revoked is True
    assert await lease_store.is_active(worker_id) is False
    assert await lease_store.get(worker_id) is None

    # Revoking again is a no-op (returns False) but doesn't error.
    revoked_again = await lease_store.revoke(worker_id, reason="deregister")
    assert revoked_again is False


async def test_disable_worker_atomically_fences_and_clears_active_state(
    lease_store: LeaseStore,
    redis_client,
):
    worker_id = f"worker-{secrets.token_hex(3)}"
    lease = await lease_store.grant(worker_id)
    heartbeat_key = f"{{{lease_store.namespace}}}:heartbeat:{worker_id}"
    await redis_client.hset(heartbeat_key, mapping={"timestamp": "now"})

    disabled = await lease_store.disable_worker(
        worker_id,
        reason="maintenance",
        heartbeat_key=heartbeat_key,
    )

    assert disabled is True
    assert await lease_store.get(worker_id) is None
    assert await lease_store.is_active(worker_id) is False
    assert await redis_client.exists(heartbeat_key) == 0
    assert await redis_client.get(lease_store.lifecycle_key(worker_id)) == "maintenance"
    revoked_key = f"{{{lease_store.namespace}}}:lease:revoked:{worker_id}"
    assert await redis_client.sismember(revoked_key, lease.lease_id)
    with pytest.raises(LeaseIneligibleError):
        await lease_store.grant(worker_id)


async def test_revoke_with_stale_generation_cannot_delete_replacement(
    lease_store: LeaseStore,
    redis_client,
):
    worker_id = f"worker-{secrets.token_hex(3)}"
    first = await lease_store.grant(worker_id, current_lease_id="")
    lease_key = f"{{{lease_store.namespace}}}:lease:data:{worker_id}"
    revoked_key = f"{{{lease_store.namespace}}}:lease:revoked:{worker_id}"
    await redis_client.hset(lease_key, "expires_at_ms", first.granted_at_ms - 1)
    replacement = await lease_store.grant(worker_id, current_lease_id="")

    revoked = await lease_store.revoke(
        worker_id,
        reason="stale-deregister",
        lease_id=first.lease_id,
    )

    assert revoked is False
    assert await lease_store.get(worker_id) == replacement
    assert await lease_store.is_active(worker_id) is True
    assert bool(await redis_client.sismember(revoked_key, first.lease_id)) is False
    assert bool(await redis_client.sismember(revoked_key, replacement.lease_id)) is False


async def test_revoke_with_matching_generation_removes_current_lease(lease_store: LeaseStore):
    worker_id = f"worker-{secrets.token_hex(3)}"
    lease = await lease_store.grant(worker_id, current_lease_id="")

    revoked = await lease_store.revoke(worker_id, lease_id=lease.lease_id)

    assert revoked is True
    assert await lease_store.get(worker_id) is None
    assert await lease_store.is_active(worker_id) is False


async def test_sweep_expired_evicts_past_due_leases(lease_store: LeaseStore):
    # Two workers grant at the same time; we'll fast-forward "now" past TTL.
    w1 = f"worker-1-{secrets.token_hex(3)}"
    w2 = f"worker-2-{secrets.token_hex(3)}"
    w_alive = f"worker-alive-{secrets.token_hex(3)}"

    lease1 = await lease_store.grant(w1, current_lease_id="")
    lease2 = await lease_store.grant(w2, current_lease_id="")
    lease_alive = await lease_store.grant(w_alive, current_lease_id="")

    # Simulate "now" 5s after expiry of the two doomed leases but well
    # before lease_alive's expiry — done by passing a now_ms in the
    # future for w1/w2 only via direct ZADD override.
    # Easiest: rewrite the score on the alive worker to far in the future
    # so it survives the sweep.
    far_future = lease_alive.expires_at_ms + 10 * lease_store.policy.ttl_ms
    await lease_store._redis.zadd(  # type: ignore[attr-defined]
        f"{{{lease_store.namespace}}}:lease:expiring",
        {w_alive: far_future},
    )

    # Use sweep with a now_ms past lease1.expires_at_ms.
    evicted = await lease_store.sweep_expired(
        now_ms=max(lease1.expires_at_ms, lease2.expires_at_ms) + 1,
    )

    # P1-03: sweep_expired now returns list[(worker_id, evicted_lease_id)].
    evicted_ids = {w for (w, _lid) in evicted}
    assert evicted_ids == {w1, w2}
    evicted_map = dict(evicted)
    # 代际 id 必须非空，且与 grant 返回的 lease_id 一致（否则回调无法代际匹配）
    assert evicted_map[w1] == lease1.lease_id
    assert evicted_map[w2] == lease2.lease_id
    assert await lease_store.is_active(w1) is False
    assert await lease_store.is_active(w2) is False
    assert await lease_store.is_active(w_alive) is True


async def test_grant_persists_metrics_json(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"

    metrics = {"cpu": 12.5, "memory": 33.0, "running_tasks": 2}
    await lease_store.grant(worker_id, current_lease_id="", metrics=metrics)

    lease_key = f"{{{lease_store.namespace}}}:lease:data:{worker_id}"
    raw = await redis_client.hget(lease_key, "metrics_json")
    assert raw, "metrics_json 字段应被写入 Hash"
    # 用最朴素的字符串包含断言，避免依赖 JSON 顺序。
    assert "cpu" in raw and "running_tasks" in raw
