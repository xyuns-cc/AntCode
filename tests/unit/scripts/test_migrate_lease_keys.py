from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest

from scripts import migrate_lease_keys
from tests.unit.scripts.migrate_lease_keys_fakes import _install_client, _Redis


@dataclass(frozen=True)
class _ConflictCase:
    policy: str
    migrated: int
    restored: list[tuple[str, int, bytes, bool]]
    deleted: list[str]


@dataclass(frozen=True)
class _InvalidSourceCase:
    redis_type: str
    pttl_ms: int
    message: str


@pytest.mark.asyncio
async def test_dry_run_scans_all_pages_without_mutating_keys(monkeypatch, capsys) -> None:
    client = _Redis(
        [
            (1, [b"antcode:lease:worker-a", "antcode:lease:expiring"]),
            (0, ["antcode:lease:active", "invalid"]),
        ]
    )
    client.add_source("antcode:lease:worker-a", payload=b"worker-a", pttl_ms=12_345)
    calls = _install_client(monkeypatch, client)

    migrated = await migrate_lease_keys.migrate("redis://127.0.0.1:6379/0", "antcode", False)

    assert migrated == 1
    assert calls == [("redis://127.0.0.1:6379/0", False)]
    assert client.scan_calls == [("antcode:lease:*", 200)]
    assert client.deleted == []
    assert client.restored == []
    assert client.zadds == []
    assert client.sadds == []
    assert client.closed is True
    output = capsys.readouterr().out
    assert "[plan] antcode:lease:worker-a -> {antcode}:lease:data:worker-a" in output
    assert "type=hash pttl=12345" in output
    assert "[index-plan]" in output


@pytest.mark.asyncio
async def test_apply_restores_current_key_then_deletes_legacy_source(monkeypatch) -> None:
    client = _Redis([(0, ["antcode:lease:worker-a", "antcode:lease:worker-b"])])
    client.add_source("antcode:lease:worker-a", payload=b"worker-a", pttl_ms=9_000)
    client.add_source("antcode:lease:worker-b", payload=b"worker-b", pttl_ms=8_000)
    client.hashes["{antcode}:lease:data:worker-a"] = {"expires_at_ms": b"1700000009000"}
    client.hashes["{antcode}:lease:data:worker-b"] = {"expires_at_ms": b"1700000008000"}
    # 迁移期间 TTL 扣减确定性：冻结 monotonic 时钟。
    monkeypatch.setattr(migrate_lease_keys, "_monotonic_ms", lambda: 1_000_000)
    _install_client(monkeypatch, client)

    migrated = await migrate_lease_keys.migrate("redis://test", "antcode", True)

    assert migrated == 2
    assert client.restored == [
        ("{antcode}:lease:data:worker-a", 9_000, b"worker-a", False),
        ("{antcode}:lease:data:worker-b", 8_000, b"worker-b", False),
    ]
    # DR-04: 迁移后重建新版 hash-tag 索引，并在无遗留旧 key 时删除旧索引。
    assert client.zadds == [
        ("{antcode}:lease:expiring", {"worker-a": 1700000009000}),
        ("{antcode}:lease:expiring", {"worker-b": 1700000008000}),
    ]
    assert client.sadds == [
        ("{antcode}:lease:active", "worker-a"),
        ("{antcode}:lease:active", "worker-b"),
    ]
    assert client.deleted == [
        "antcode:lease:worker-a",
        "antcode:lease:worker-b",
        "antcode:lease:expiring",
        "antcode:lease:active",
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_existing_target_is_an_explicit_conflict_by_default(monkeypatch) -> None:
    source = "antcode:lease:worker-a"
    target = "{antcode}:lease:data:worker-a"
    client = _Redis([(0, [source])])
    client.add_source(source)
    client.existing_targets.add(target)
    _install_client(monkeypatch, client)

    with pytest.raises(migrate_lease_keys.MigrationConflictError, match="目标已存在"):
        await migrate_lease_keys.migrate("redis://test", "antcode", True)

    assert client.restored == []
    assert client.deleted == []
    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        _ConflictCase("skip", 0, [], []),
        _ConflictCase(
            policy="replace",
            migrated=1,
            restored=[("{antcode}:lease:data:worker-a", 20_000, b"redis-dump", True)],
            # replace 后无遗留旧 key → 旧索引一并删除（DR-04）。
            deleted=["antcode:lease:worker-a", "antcode:lease:expiring", "antcode:lease:active"],
        ),
    ],
)
async def test_existing_target_requires_an_explicit_policy(
    monkeypatch,
    case: _ConflictCase,
) -> None:
    source = "antcode:lease:worker-a"
    target = "{antcode}:lease:data:worker-a"
    client = _Redis([(0, [source])])
    client.add_source(source)
    client.existing_targets.add(target)
    monkeypatch.setattr(migrate_lease_keys, "_monotonic_ms", lambda: 1_000_000)
    _install_client(monkeypatch, client)

    count = await migrate_lease_keys.migrate(
        "redis://test",
        "antcode",
        True,
        on_conflict=case.policy,
    )

    assert count == case.migrated
    assert client.restored == case.restored
    assert client.deleted == case.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        _InvalidSourceCase("string", 20_000, "类型必须为 hash"),
        _InvalidSourceCase("hash", -1, "缺少有效 TTL"),
    ],
)
async def test_invalid_legacy_source_is_rejected(
    monkeypatch,
    case: _InvalidSourceCase,
) -> None:
    source = "antcode:lease:worker-a"
    client = _Redis([(0, [source])])
    client.add_source(source, redis_type=case.redis_type, pttl_ms=case.pttl_ms)
    _install_client(monkeypatch, client)

    with pytest.raises((TypeError, RuntimeError), match=case.message):
        await migrate_lease_keys.migrate("redis://test", "antcode", True)

    assert client.restored == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_unknown_conflict_policy_is_rejected_before_connecting(monkeypatch) -> None:
    client = _Redis([])
    calls = _install_client(monkeypatch, client)

    with pytest.raises(ValueError, match="未知目标冲突策略"):
        await migrate_lease_keys.migrate(
            "redis://test",
            "antcode",
            False,
            on_conflict="invalid",
        )

    assert calls == []
    assert client.closed is False


def test_main_requires_redis_url(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["migrate_lease_keys.py"])

    with pytest.raises(SystemExit) as exc_info:
        migrate_lease_keys.main()

    assert exc_info.value.code == 2
