from __future__ import annotations

import json

import pytest

from scripts.crawl_redis_upgrade_contract import (
    LegacyKeyKind,
    UpgradeMode,
    UpgradeRequest,
    parse_legacy_key,
)
from scripts.crawl_redis_upgrade_scan import build_report
from scripts.migrate_crawl_redis import _required_setting
from tests.unit.scripts.crawl_redis_upgrade_fakes import UpgradeRedisFake

EXPECTED_UNCONSUMED = 2
MULTI_PAGE_STREAM_ENTRIES = 501


def _existing_request(**changes) -> UpgradeRequest:
    values = {
        "mode": UpgradeMode.EXISTING_UPGRADE,
        "namespace": "tenant-a",
        "writers_stopped": True,
    }
    values.update(changes)
    return UpgradeRequest(**values)


def _envelope(version: int) -> str:
    return json.dumps({"version": version, "kind": "worker", "algorithm": "test", "ciphertext": "value"})


def test_mode_contract_requires_explicit_stop_and_disallows_fresh_apply() -> None:
    with pytest.raises(ValueError, match="writers"):
        _existing_request(writers_stopped=False).validate()
    with pytest.raises(ValueError, match="不允许 --apply"):
        UpgradeRequest(UpgradeMode.FRESH_DEPLOY, "antcode", apply=True).validate()


def test_container_runtime_settings_are_explicit_and_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_NAMESPACE", raising=False)

    with pytest.raises(ValueError, match="REDIS_URL"):
        _required_setting(None, "REDIS_URL")
    with pytest.raises(ValueError, match="REDIS_NAMESPACE"):
        _required_setting(" ", "REDIS_NAMESPACE")

    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("REDIS_NAMESPACE", "tenant-a")
    assert _required_setting(None, "REDIS_URL") == "redis://redis:6379/0"
    assert _required_setting(None, "REDIS_NAMESPACE") == "tenant-a"
    with pytest.raises(ValueError, match="REDIS_URL"):
        _required_setting(" ", "REDIS_URL")


@pytest.mark.parametrize(
    "case",
    [
        ("rule:project-1:stream:5", LegacyKeyKind.STREAM, "project-1", None),
        ("rule:project-1:progress:batch-1", LegacyKeyKind.PROGRESS, "project-1", "batch-1"),
        ("rule:project-1:checkpoint:batch-1", LegacyKeyKind.CHECKPOINT, "project-1", "batch-1"),
        ("rule:project-1:dedup", LegacyKeyKind.DEDUP, "project-1", None),
        ("crawl:workers:registry", LegacyKeyKind.CRAWL_RUNTIME, None, None),
    ],
)
def test_legacy_key_parser(case) -> None:
    key, kind, project, batch = case
    parsed = parse_legacy_key(key)
    assert (parsed.kind, parsed.project_id, parsed.batch_id) == (kind, project, batch)


@pytest.mark.asyncio
async def test_paused_hash_and_exact_set_are_migratable() -> None:
    redis = UpgradeRedisFake()
    redis.hashes["rule:project-1:progress:batch-1"] = {b"completed": b"7"}
    redis.hashes["rule:project-1:checkpoint:batch-1"] = {b"offset": b"8"}
    redis.sets["rule:project-1:dedup"] = {b"a", b"b"}
    request = _existing_request(
        paused_batches=frozenset({("project-1", "batch-1")}),
        paused_projects=frozenset({"project-1"}),
    )

    report = await build_report(redis, request)

    assert report.safe is True
    assert {item.target for item in report.state_keys} == {
        "{tenant-a:crawl:project-1:batch-1}:progress",
        "{tenant-a:crawl:project-1:batch-1}:checkpoint",
        "{tenant-a:crawl:project-1}:dedup",
    }
    assert {item.items for item in report.state_keys} == {1, 2}


@pytest.mark.asyncio
async def test_different_existing_hash_target_blocks_before_migration() -> None:
    redis = UpgradeRedisFake()
    source = "rule:project-1:progress:batch-1"
    target = "{tenant-a:crawl:project-1:batch-1}:progress"
    redis.hashes[source] = {b"completed": b"7"}
    redis.hashes[target] = {b"completed": b"8"}
    request = _existing_request(paused_batches=frozenset({("project-1", "batch-1")}))

    report = await build_report(redis, request)

    finding = next(item for item in report.blockers if item.code == "target_content_conflict")
    assert finding.key == target


@pytest.mark.asyncio
async def test_unpaused_state_and_redisbloom_module_fail_closed() -> None:
    redis = UpgradeRedisFake()
    redis.hashes["rule:project-1:progress:batch-1"] = {b"completed": b"7"}
    redis.explicit_types["rule:project-1:dedup"] = "MBbloom--"

    report = await build_report(redis, _existing_request())

    codes = {item.code for item in report.blockers}
    assert "state_not_declared_paused" in codes
    assert "unsupported_state_type" in codes
    bloom = next(item for item in report.blockers if item.key.endswith(":dedup"))
    assert "cannot be converted" in bloom.detail


@pytest.mark.asyncio
async def test_state_with_disappearing_ttl_is_rejected_before_apply() -> None:
    redis = UpgradeRedisFake()
    key = "rule:project-1:progress:batch-1"
    redis.hashes[key] = {b"completed": b"7"}
    redis.ttls[key] = 0
    request = _existing_request(paused_batches=frozenset({("project-1", "batch-1")}))

    report = await build_report(redis, request)

    assert any(item.code == "invalid_source_ttl" for item in report.blockers)


@pytest.mark.asyncio
async def test_stream_reports_group_pel_and_unconsumed_counts() -> None:
    redis = UpgradeRedisFake()
    key = "rule:project-1:stream:5"
    redis.streams[key] = [("1-0", {"url": "https://example.test"})]
    redis.groups[key] = [{"name": "crawl_workers", "lag": EXPECTED_UNCONSUMED}]
    redis.pending[(key, "crawl_workers")] = 1

    report = await build_report(redis, _existing_request())

    stream = next(item for item in report.streams if item.key == key)
    assert stream.groups[0].pending == 1
    assert stream.groups[0].unconsumed == EXPECTED_UNCONSUMED
    assert any(item.code == "execution_queue_not_drained" for item in report.blockers)


@pytest.mark.asyncio
async def test_missing_stream_lag_cannot_be_treated_as_drained() -> None:
    redis = UpgradeRedisFake()
    key = "rule:project-1:stream:5"
    redis.streams[key] = [("1-0", {"url": "https://example.test"})]
    redis.groups[key] = [{"name": "crawl_workers"}]

    report = await build_report(redis, _existing_request())

    assert any(item.code == "unknown_stream_lag" for item in report.blockers)


@pytest.mark.asyncio
async def test_retained_ready_entry_with_v1_envelope_blocks_even_when_group_is_drained() -> None:
    redis = UpgradeRedisFake()
    key = "tenant-a:task:ready:worker-1"
    redis.streams[key] = [("1-0", {"sensitive_payload_envelope": _envelope(1)})]
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": 0}]

    report = await build_report(redis, _existing_request())

    stream = next(item for item in report.streams if item.key == key)
    assert stream.envelope_v1 == 1
    assert any(item.code == "v1_envelope" for item in report.blockers)
    assert not any(item.code == "execution_queue_not_drained" for item in report.blockers)


@pytest.mark.asyncio
async def test_ready_envelope_scan_continues_across_all_pages() -> None:
    redis = UpgradeRedisFake()
    key = "tenant-a:task:ready:worker-1"
    redis.streams[key] = [
        (f"{index}-0", {"sensitive_payload_envelope": _envelope(2)}) for index in range(1, MULTI_PAGE_STREAM_ENTRIES)
    ]
    redis.streams[key].append((f"{MULTI_PAGE_STREAM_ENTRIES}-0", {"sensitive_payload_envelope": _envelope(1)}))
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": 0}]

    report = await build_report(redis, _existing_request())

    stream = next(item for item in report.streams if item.key == key)
    assert stream.entries == MULTI_PAGE_STREAM_ENTRIES
    assert stream.envelope_v1 == 1


@pytest.mark.asyncio
async def test_v2_ready_entry_is_allowed_after_pel_and_lag_reach_zero() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.streams[key] = [("1-0", {"sensitive_payload_envelope": _envelope(2)})]
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": 0}]

    report = await build_report(redis, _existing_request())

    assert report.safe is True
    assert report.streams[0].entries == 1


@pytest.mark.asyncio
async def test_ready_dead_letter_companion_is_not_misclassified_as_execution_queue() -> None:
    redis = UpgradeRedisFake()
    dlq = "{tenant-a}:task:ready:worker-1:{dlq}:task:dead_letter"
    redis.streams[dlq] = [("1-0", {"reason": "failed"})]

    report = await build_report(redis, _existing_request())

    assert report.safe is True
    assert report.streams == ()


@pytest.mark.asyncio
async def test_ready_main_key_wrong_type_is_a_reported_blocker() -> None:
    redis = UpgradeRedisFake()
    key = "tenant-a:task:ready:worker-1"
    redis.hashes[key] = {"unexpected": "value"}

    report = await build_report(redis, _existing_request())

    finding = next(item for item in report.blockers if item.key == key)
    assert finding.code == "execution_store_type"
    assert "actual=hash" in finding.detail


@pytest.mark.asyncio
async def test_plaintext_redispatch_member_is_reported_and_blocks_upgrade() -> None:
    redis = UpgradeRedisFake()
    key = "tenant-a:task:redispatch"
    redis.zsets[key] = [(json.dumps({"params": {"setting": "legacy-plaintext-value"}}), 1.0)]

    report = await build_report(redis, _existing_request())

    assert report.execution_stores[0].entries == 1
    assert report.execution_stores[0].envelope_unsupported == 1
    assert {item.code for item in report.blockers} >= {
        "execution_queue_not_drained",
        "unsupported_envelope",
    }


@pytest.mark.asyncio
async def test_fresh_deploy_rejects_legacy_and_current_crawl_data() -> None:
    redis = UpgradeRedisFake()
    redis.hashes["rule:project-1:progress:batch-1"] = {"value": "1"}
    redis.sets["{tenant-a:crawl:project-1}:dedup"] = {"fingerprint"}
    redis.hashes["tenant-a:crawl:workers:registry"] = {"worker-1": "active"}
    request = UpgradeRequest(UpgradeMode.FRESH_DEPLOY, "tenant-a")

    report = await build_report(redis, request)

    codes = {item.code for item in report.blockers}
    assert "fresh_deploy_has_legacy_data" in codes
    assert "fresh_deploy_has_current_data" in codes
    current = next(item for item in report.blockers if item.code == "fresh_deploy_has_current_data")
    assert "keys=2" in current.detail


@pytest.mark.asyncio
async def test_empty_fresh_deploy_is_safe_without_stop_confirmation() -> None:
    redis = UpgradeRedisFake()

    report = await build_report(redis, UpgradeRequest(UpgradeMode.FRESH_DEPLOY, "tenant-a"))

    assert report.safe is True


@pytest.mark.asyncio
async def test_legacy_crawl_runtime_key_requires_explicit_disposition() -> None:
    redis = UpgradeRedisFake()
    key = "crawl:election:master"
    redis.explicit_types[key] = "string"

    report = await build_report(redis, _existing_request())

    finding = next(item for item in report.blockers if item.key == key)
    assert finding.code == "unsupported_legacy_key"
