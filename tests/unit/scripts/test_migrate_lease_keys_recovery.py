"""migrate_lease_keys 故障与恢复路径测试：连接失败、关闭失败与崩溃后重跑。"""

from __future__ import annotations

import pytest

from scripts import migrate_lease_keys
from tests.unit.scripts.migrate_lease_keys_fakes import _install_client, _Redis


@pytest.mark.asyncio
async def test_redis_connection_failure_is_propagated(monkeypatch) -> None:
    class _FailingRedis(_Redis):
        async def ping(self) -> bool:
            raise ConnectionError("redis unavailable")

    client = _FailingRedis([])
    _install_client(monkeypatch, client)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await migrate_lease_keys.migrate("redis://test", "antcode", False)

    assert client.closed is True


@pytest.mark.asyncio
async def test_non_async_redis_client_is_rejected(monkeypatch) -> None:
    client = _Redis([])
    client.ping = lambda: True
    _install_client(monkeypatch, client)

    with pytest.raises(TypeError, match="非 awaitable"):
        await migrate_lease_keys.migrate("redis://test", "antcode", False)

    assert client.closed is True


@pytest.mark.asyncio
async def test_close_failure_is_propagated_after_success(monkeypatch) -> None:
    client = _Redis([(0, [])], close_error=OSError("redis close failed"))
    _install_client(monkeypatch, client)

    with pytest.raises(OSError, match="redis close failed"):
        await migrate_lease_keys.migrate("redis://test", "antcode", False)

    assert client.closed is True


@pytest.mark.asyncio
async def test_migration_and_close_failures_are_both_reported(monkeypatch) -> None:
    class _FailingRedis(_Redis):
        async def ping(self) -> bool:
            raise ConnectionError("redis unavailable")

    client = _FailingRedis([], close_error=OSError("redis close failed"))
    _install_client(monkeypatch, client)

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await migrate_lease_keys.migrate("redis://test", "antcode", False)

    assert [str(error) for error in exc_info.value.exceptions] == [
        "redis unavailable",
        "redis close failed",
    ]


@pytest.mark.asyncio
async def test_rerun_after_crash_rebuilds_indexes_from_target_scan(monkeypatch) -> None:
    # P1-DR-03: 上次运行在"源已 DEL、索引未建"之间崩溃——重跑时源 key
    # 已不存在（migrated 列表为空），索引仍必须从目标 Hash SCAN 补齐。
    client = _Redis([(0, [])])
    client.hashes["{antcode}:lease:data:worker-a"] = {"expires_at_ms": b"1700000009000"}
    _install_client(monkeypatch, client)

    migrated = await migrate_lease_keys.migrate("redis://test", "antcode", True)

    assert migrated == 0
    assert client.zadds == [("{antcode}:lease:expiring", {"worker-a": 1700000009000})]
    assert client.sadds == [("{antcode}:lease:active", "worker-a")]
