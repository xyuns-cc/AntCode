from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.lease_service import (
    _GRANT_LUA,
    _SWEEP_DELETE_LUA,
    LEASE_RECORD_RETENTION_MS,
    LeaseConflictError,
    LeaseStore,
)


@dataclass(frozen=True)
class _CurrentLeaseCase:
    exists: int
    stored_lease_id: str
    pttl_ms: int
    expected: bool


def test_public_lease_key_matches_the_authoritative_key_format() -> None:
    store = LeaseStore(MagicMock(), namespace="tenant")

    assert store.lease_key("worker-a") == "{tenant}:lease:data:worker-a"


@pytest.mark.asyncio
async def test_grant_updates_primary_record_and_indexes_in_one_script():
    redis = MagicMock()
    store = LeaseStore(redis, namespace="tenant")
    # P1-GW-01 (round6): Lua 返回值增加第 5 项 sequence
    store._evalsha_grant = AsyncMock(return_value=["lease-id", "2000", "1000", "new", "7"])

    lease = await store.grant("worker-a")

    keys = store._evalsha_grant.await_args.args[0]
    assert keys == [
        "{tenant}:lease:data:worker-a",
        "{tenant}:lease:revoked:worker-a",
        "{tenant}:lease:expiring",
        "{tenant}:lease:active",
        "{tenant}:lease:sequence",  # P1-GW-01 (round6): 全局 seq 计数器
    ]
    args = store._evalsha_grant.await_args.args[1]
    assert args[3:] == ["30000", "5000", "", ""]
    assert lease.lease_id == "lease-id"
    expected_sequence = 7  # P1-GW-01 (round6): sequence 单调 tie-breaker,mock 固定值
    assert lease.sequence == expected_sequence
    redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_grant_atomically_persists_capabilities_with_lease():
    store = LeaseStore(MagicMock(), namespace="tenant")
    store._evalsha_grant = AsyncMock(return_value=["lease-id", "2000", "1000", "new", "7"])

    await store.grant(
        "worker-a",
        metrics={"cpu": 1.5},
        capabilities={"task_types": ["code", "rule"]},
    )

    args = store._evalsha_grant.await_args.args[1]
    assert args[-2:] == ['{"cpu":1.5}', '{"task_types":["code","rule"]}']
    assert "'capabilities_json', capabilities_json" in _GRANT_LUA


@pytest.mark.asyncio
async def test_grant_explicit_empty_capabilities_clear_the_previous_snapshot():
    store = LeaseStore(MagicMock(), namespace="tenant")
    store._evalsha_grant = AsyncMock(return_value=["lease-id", "2000", "1000", "renewed", "7"])

    await store.grant("worker-a", current_lease_id="lease-id", capabilities={})

    assert store._evalsha_grant.await_args.args[1][-1] == "{}"


def test_grant_lua_uses_redis_time_and_physical_ttl_as_authority() -> None:
    assert "redis.call('TIME')" in _GRANT_LUA
    assert "redis.call('PEXPIRE', lease_key, ttl_ms + record_retention_ms)" in _GRANT_LUA
    assert "local expires_at_ms = now_ms + ttl_ms" in _GRANT_LUA
    assert "redis.call('ZADD', expiring_key, expires_at_ms, worker_id)" in _GRANT_LUA
    assert "local now_ms" not in "\n".join(line for line in _GRANT_LUA.splitlines() if "ARGV" in line)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_lease_id", ["", "stale-lease-id"])
async def test_grant_maps_conflict_without_returning_a_lease(current_lease_id: str):
    store = LeaseStore(MagicMock(), namespace="tenant")
    store._evalsha_grant = AsyncMock(return_value=["", "", "", "conflict"])

    with pytest.raises(LeaseConflictError) as caught:
        await store.grant("worker-a", current_lease_id=current_lease_id)

    assert caught.value.worker_id == "worker-a"
    assert caught.value.current_lease_id == current_lease_id


@pytest.mark.asyncio
async def test_grant_rejects_unknown_script_outcome():
    store = LeaseStore(MagicMock(), namespace="tenant")
    store._evalsha_grant = AsyncMock(return_value=["", "", "", "unexpected"])

    with pytest.raises(RuntimeError, match="未知 outcome"):
        await store.grant("worker-a")


@pytest.mark.asyncio
async def test_revoke_updates_primary_record_and_indexes_in_one_script():
    redis = MagicMock()
    store = LeaseStore(redis, namespace="tenant")
    store._evalsha_revoke = AsyncMock(return_value=1)

    assert await store.revoke("worker-a", lease_id="lease-id") is True

    keys = store._evalsha_revoke.await_args.args[0]
    assert keys[-2:] == [
        "{tenant}:lease:expiring",
        "{tenant}:lease:active",
    ]
    assert store._evalsha_revoke.await_args.args[1][1] == "lease-id"
    redis.pipeline.assert_not_called()


def test_sweep_lua_returns_the_persisted_lease_id():
    assert "redis.call('HGET', lease_key, 'lease_id')" in _SWEEP_DELETE_LUA
    assert "redis.call('HGET', lease_key, 'current_lease_id')" not in _SWEEP_DELETE_LUA


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        _CurrentLeaseCase(1, "lease-1", LEASE_RECORD_RETENTION_MS + 1, True),
        _CurrentLeaseCase(0, "lease-1", 10_000, False),
        _CurrentLeaseCase(1, "lease-other", 10_000, False),
        _CurrentLeaseCase(1, "lease-1", LEASE_RECORD_RETENTION_MS, False),
        _CurrentLeaseCase(1, "lease-1", -1, False),
        _CurrentLeaseCase(1, "lease-1", -2, False),
    ],
)
async def test_is_current_requires_exact_generation_and_positive_redis_ttl(
    case: _CurrentLeaseCase,
) -> None:
    redis = MagicMock()
    pipeline = MagicMock()
    # P1-DR-02: is_current 现在多一步 sismember revoked check(未 revoke → 0)
    pipeline.execute = AsyncMock(return_value=[0, case.exists, case.stored_lease_id, case.pttl_ms])
    redis.pipeline.return_value = pipeline
    store = LeaseStore(redis, namespace="tenant")

    assert await store.is_current("worker-a", "lease-1") is case.expected
    redis.pipeline.assert_called_once_with(transaction=True)
    pipeline.sismember.assert_called_once_with("{tenant}:lease:revoked:worker-a", "lease-1")
    pipeline.exists.assert_called_once_with("{tenant}:lease:data:worker-a")
    pipeline.hget.assert_called_once_with("{tenant}:lease:data:worker-a", "lease_id")
    pipeline.pttl.assert_called_once_with("{tenant}:lease:data:worker-a")


@pytest.mark.asyncio
async def test_is_current_returns_false_when_lease_in_revoked_set() -> None:
    """P1-DR-02:被 revoke 的 lease_id 即使 lease_key 仍存在也返回 False。"""
    redis = MagicMock()
    pipeline = MagicMock()
    # sismember=1(在 revoked set 里),其余字段无关紧要
    pipeline.execute = AsyncMock(return_value=[1, 1, b"lease-1", LEASE_RECORD_RETENTION_MS + 10_000])
    redis.pipeline.return_value = pipeline
    store = LeaseStore(redis, namespace="tenant")

    assert await store.is_current("worker-a", "lease-1") is False


@pytest.mark.asyncio
async def test_get_can_exclude_logically_expired_retention_records() -> None:
    """M2 回归：retention 窗口内的过期残留 Hash 不能被当成有效 lease。"""
    redis = MagicMock()
    redis.time = AsyncMock(return_value=(1_700_000_000, 0))  # now = 1_700_000_000_000 ms
    redis.hgetall = AsyncMock(
        return_value={
            b"lease_id": b"lease-stale",
            b"expires_at_ms": b"1699999999000",  # 已逻辑过期
            b"granted_at_ms": b"1699999969000",
        }
    )
    store = LeaseStore(redis, namespace="tenant")

    # 默认行为不变：仍返回残留记录（其它调用方依赖）
    lease = await store.get("worker-a")
    assert lease is not None and lease.lease_id == "lease-stale"
    # include_expired=False：过期记录视同不存在
    assert await store.get("worker-a", include_expired=False) is None

    # 未过期时 include_expired=False 正常返回
    redis.hgetall = AsyncMock(
        return_value={
            b"lease_id": b"lease-live",
            b"expires_at_ms": b"1700000030000",
            b"granted_at_ms": b"1700000000000",
        }
    )
    live = await store.get("worker-a", include_expired=False)
    assert live is not None and live.lease_id == "lease-live"


@pytest.mark.asyncio
async def test_sweep_default_cutoff_uses_redis_time() -> None:
    redis = MagicMock()
    redis.time = AsyncMock(return_value=(1_700_000_000, 123_456))
    redis.zrangebyscore = AsyncMock(return_value=[])
    store = LeaseStore(redis, namespace="tenant")

    assert await store.sweep_expired() == []

    assert redis.zrangebyscore.await_args.kwargs["max"] == 1_700_000_000_123
