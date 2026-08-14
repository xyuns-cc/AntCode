from __future__ import annotations

import pytest

from scripts.crawl_redis_upgrade_contract import StateKeyStats, UpgradeBlocked, UpgradeMode, UpgradeRequest
from scripts.crawl_redis_upgrade_migration import migrate_state_keys
from scripts.migrate_crawl_redis import execute
from tests.unit.scripts.crawl_redis_upgrade_fakes import UpgradeRedisFake


def _state(source: str, target: str, redis_type: str) -> StateKeyStats:
    return StateKeyStats(source, target, redis_type, redis_type, 1, -1, True)


@pytest.mark.asyncio
async def test_hash_restore_is_verified_before_source_delete() -> None:
    redis = UpgradeRedisFake()
    source = "rule:project-1:progress:batch-1"
    target = "{tenant-a:crawl:project-1:batch-1}:progress"
    redis.hashes[source] = {b"completed": b"3"}

    migrated = await migrate_state_keys(redis, (_state(source, target, "hash"),))

    assert migrated == (source,)
    assert redis.hashes[target] == {b"completed": b"3"}
    assert redis.restored == [(target, 0, f"hash:{source}".encode(), False)]
    assert redis.persisted == [target]
    assert redis.deleted == [source]


@pytest.mark.asyncio
async def test_equal_hash_target_makes_crash_recovery_idempotent() -> None:
    redis = UpgradeRedisFake()
    source = "rule:project-1:checkpoint:batch-1"
    target = "{tenant-a:crawl:project-1:batch-1}:checkpoint"
    value = {b"offset": b"9"}
    redis.hashes[source] = dict(value)
    redis.hashes[target] = dict(value)
    redis.ttls[target] = 10_000

    await migrate_state_keys(redis, (_state(source, target, "hash"),))

    assert redis.restored == []
    assert redis.hashes[target] == value
    assert redis.persisted == [target]
    assert target not in redis.ttls
    assert redis.deleted == [source]


@pytest.mark.asyncio
async def test_set_migration_unions_partial_target_and_is_retryable() -> None:
    redis = UpgradeRedisFake()
    source = "rule:project-1:dedup"
    target = "{tenant-a:crawl:project-1}:dedup"
    redis.sets[source] = {b"a", b"b"}
    redis.sets[target] = {b"a", b"new-generation"}
    redis.ttls[target] = 10_000

    await migrate_state_keys(redis, (_state(source, target, "set"),))

    assert redis.sets[target] == {b"a", b"b", b"new-generation"}
    assert redis.persisted == [target]
    assert target not in redis.ttls
    assert redis.deleted == [source]


@pytest.mark.asyncio
async def test_blocked_preflight_never_mutates_state() -> None:
    redis = UpgradeRedisFake()
    source = "rule:project-1:progress:batch-1"
    redis.hashes[source] = {b"completed": b"3"}
    request = UpgradeRequest(
        UpgradeMode.EXISTING_UPGRADE,
        "tenant-a",
        apply=True,
        writers_stopped=True,
    )

    with pytest.raises(UpgradeBlocked) as exc_info:
        await execute(redis, request)

    assert exc_info.value.report.blockers[0].code == "state_not_declared_paused"
    assert redis.deleted == []
    assert redis.restored == []


@pytest.mark.asyncio
async def test_execute_apply_migrates_all_preflighted_state() -> None:
    redis = UpgradeRedisFake()
    progress = "rule:project-1:progress:batch-1"
    dedup = "rule:project-1:dedup"
    redis.hashes[progress] = {b"completed": b"3"}
    redis.sets[dedup] = {b"fingerprint"}
    request = UpgradeRequest(
        UpgradeMode.EXISTING_UPGRADE,
        "tenant-a",
        apply=True,
        writers_stopped=True,
        paused_batches=frozenset({("project-1", "batch-1")}),
        paused_projects=frozenset({"project-1"}),
    )

    report = await execute(redis, request)

    assert report.safe is True
    assert set(report.migrated_sources) == {progress, dedup}
    assert progress not in redis.keys()
    assert dedup not in redis.keys()
