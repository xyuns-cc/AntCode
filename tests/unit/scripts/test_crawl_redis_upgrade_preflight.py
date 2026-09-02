from __future__ import annotations

import json

import pytest

from scripts.crawl_redis_upgrade_contract import UpgradeBlocked, UpgradeRequest
from scripts.crawl_redis_upgrade_scan import build_report
from scripts.migrate_crawl_redis import _required_setting, execute
from tests.unit.scripts.crawl_redis_upgrade_fakes import UpgradeRedisFake

EXPECTED_UNCONSUMED = 2
MULTI_PAGE_STREAM_ENTRIES = 501


def _request() -> UpgradeRequest:
    return UpgradeRequest("tenant-a")


def _envelope(version: int) -> str:
    return json.dumps({"version": version, "kind": "worker", "algorithm": "test", "ciphertext": "value"})


def test_namespace_contract_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="namespace"):
        UpgradeRequest("tenant a").validate()


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


@pytest.mark.asyncio
async def test_stream_reports_group_pel_and_unconsumed_counts() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.streams[key] = [("1-0", {"sensitive_payload_envelope": _envelope(2)})]
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": EXPECTED_UNCONSUMED}]
    redis.pending[(key, "tenant-a-workers")] = 1

    report = await build_report(redis, _request())

    stream = next(item for item in report.streams if item.key == key)
    assert stream.groups[0].pending == 1
    assert stream.groups[0].unconsumed == EXPECTED_UNCONSUMED
    assert any(item.code == "execution_queue_not_drained" for item in report.blockers)


@pytest.mark.asyncio
async def test_missing_stream_lag_cannot_be_treated_as_drained() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.streams[key] = [("1-0", {"sensitive_payload_envelope": _envelope(2)})]
    redis.groups[key] = [{"name": "tenant-a-workers"}]

    report = await build_report(redis, _request())

    assert any(item.code == "unknown_stream_lag" for item in report.blockers)


@pytest.mark.asyncio
async def test_retained_ready_entry_with_v1_envelope_blocks_even_when_group_is_drained() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.streams[key] = [("1-0", {"sensitive_payload_envelope": _envelope(1)})]
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": 0}]

    report = await build_report(redis, _request())

    stream = next(item for item in report.streams if item.key == key)
    assert stream.envelope_v1 == 1
    assert any(item.code == "v1_envelope" for item in report.blockers)
    assert not any(item.code == "execution_queue_not_drained" for item in report.blockers)


@pytest.mark.asyncio
async def test_ready_envelope_scan_continues_across_all_pages() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.streams[key] = [
        (f"{index}-0", {"sensitive_payload_envelope": _envelope(2)}) for index in range(1, MULTI_PAGE_STREAM_ENTRIES)
    ]
    redis.streams[key].append((f"{MULTI_PAGE_STREAM_ENTRIES}-0", {"sensitive_payload_envelope": _envelope(1)}))
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": 0}]

    report = await build_report(redis, _request())

    stream = next(item for item in report.streams if item.key == key)
    assert stream.entries == MULTI_PAGE_STREAM_ENTRIES
    assert stream.envelope_v1 == 1


@pytest.mark.asyncio
async def test_v2_ready_entry_is_allowed_after_pel_and_lag_reach_zero() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.streams[key] = [("1-0", {"sensitive_payload_envelope": _envelope(2)})]
    redis.groups[key] = [{"name": "tenant-a-workers", "lag": 0}]
    # 无 hash-tag 的 key 不是本产品写得出来的形状（唯一的 ready 写入方是
    # lease_fenced_ready_publish 的 Lua，key 恒为 task_ready_stream 的输出），不扫。
    redis.streams["tenant-a:task:ready:worker-1"] = [("1-0", {"sensitive_payload_envelope": _envelope(1)})]
    redis.zsets["tenant-a:task:redispatch"] = [("{}", 1.0)]

    report = await build_report(redis, _request())

    assert report.safe is True
    assert [item.key for item in report.streams] == [key]
    assert report.execution_stores == ()


@pytest.mark.asyncio
async def test_ready_dead_letter_companion_is_not_misclassified_as_execution_queue() -> None:
    redis = UpgradeRedisFake()
    dlq = "{tenant-a}:task:ready:worker-1:{dlq}:task:dead_letter"
    redis.streams[dlq] = [("1-0", {"reason": "failed"})]

    report = await build_report(redis, _request())

    assert report.safe is True
    assert report.streams == ()


@pytest.mark.asyncio
async def test_ready_main_key_wrong_type_is_a_reported_blocker() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:ready:worker-1"
    redis.hashes[key] = {"unexpected": "value"}

    report = await build_report(redis, _request())

    finding = next(item for item in report.blockers if item.key == key)
    assert finding.code == "execution_store_type"
    assert "actual=hash" in finding.detail


@pytest.mark.asyncio
async def test_plaintext_redispatch_member_is_reported_and_blocks_startup() -> None:
    redis = UpgradeRedisFake()
    key = "{tenant-a}:task:redispatch"
    redis.zsets[key] = [(json.dumps({"params": {"setting": "legacy-plaintext-value"}}), 1.0)]

    report = await build_report(redis, _request())

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

    report = await build_report(redis, _request())

    codes = {item.code for item in report.blockers}
    assert "fresh_deploy_has_legacy_data" in codes
    assert "fresh_deploy_has_current_data" in codes
    current = next(item for item in report.blockers if item.code == "fresh_deploy_has_current_data")
    assert "keys=2" in current.detail


@pytest.mark.asyncio
async def test_legacy_crawl_runtime_key_is_reported_as_legacy_data() -> None:
    redis = UpgradeRedisFake()
    key = "crawl:election:master"
    redis.explicit_types[key] = "string"

    report = await build_report(redis, _request())

    assert report.legacy_keys == (key,)
    assert any(item.code == "fresh_deploy_has_legacy_data" for item in report.blockers)


@pytest.mark.asyncio
async def test_empty_redis_is_safe() -> None:
    report = await build_report(UpgradeRedisFake(), _request())

    assert report.safe is True


@pytest.mark.asyncio
async def test_blocked_preflight_fails_closed_before_the_control_plane_starts() -> None:
    redis = UpgradeRedisFake()
    redis.hashes["rule:project-1:progress:batch-1"] = {b"completed": b"3"}

    with pytest.raises(UpgradeBlocked) as exc_info:
        await execute(redis, _request())

    assert {item.code for item in exc_info.value.report.blockers} == {"fresh_deploy_has_legacy_data"}
