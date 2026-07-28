import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.app.lifecycle import Lifecycle

LEASE_TTL_MS = 30_000
LEASE_RENEW_AFTER_MS = 10_000


def _future_expiry() -> int:
    return int(time.time() * 1000) + LEASE_TTL_MS


def _container(transport, heartbeat_reporter=None, *, heartbeat_interval=30):
    return SimpleNamespace(
        transport=transport,
        config=SimpleNamespace(heartbeat_interval=heartbeat_interval),
        runtime_manager=None,
        executor=None,
        observability_server=None,
        heartbeat_reporter=heartbeat_reporter,
        engine=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        SimpleNamespace(renew_after_ms=1_000, configured_interval=30, expected_interval=1),
        SimpleNamespace(renew_after_ms=1_001, configured_interval=30, expected_interval=1),
        SimpleNamespace(renew_after_ms=LEASE_RENEW_AFTER_MS, configured_interval=3, expected_interval=3),
        SimpleNamespace(renew_after_ms=LEASE_RENEW_AFTER_MS, configured_interval=30, expected_interval=10),
    ],
)
async def test_startup_uses_safe_lease_renewal_interval(
    monkeypatch,
    case,
):
    monkeypatch.setattr(
        "antcode_worker.runtime.mise_bootstrap.ensure_mise",
        AsyncMock(),
    )
    transport = MagicMock(is_connected=True)
    transport.start = AsyncMock(return_value=True)
    transport.stop = AsyncMock()
    transport.deregister = AsyncMock()
    transport.lease_renew = AsyncMock(return_value=("lease-1", _future_expiry(), case.renew_after_ms, False))
    heartbeat = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    lifecycle = Lifecycle()

    await lifecycle.startup(
        _container(
            transport,
            heartbeat,
            heartbeat_interval=case.configured_interval,
        )
    )

    heartbeat.start.assert_awaited_once_with(interval=case.expected_interval)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lease_response",
    [
        ("", _future_expiry(), LEASE_RENEW_AFTER_MS, False),
        ("lease-1", _future_expiry(), 0, False),
        ("lease-1", _future_expiry(), LEASE_RENEW_AFTER_MS, True),
        ("lease-1", 0, LEASE_RENEW_AFTER_MS, False),
    ],
)
async def test_initial_lease_failure_blocks_worker_startup(lease_response):
    transport = SimpleNamespace(lease_renew=AsyncMock(return_value=lease_response))
    lifecycle = _fast_fail_lifecycle()

    with pytest.raises(RuntimeError, match="初始 lease|renew_after_ms"):
        await lifecycle._initial_lease_renew(_container(transport))


@pytest.mark.asyncio
async def test_initial_lease_direct_mode_rechecks_authoritative_clock(monkeypatch):
    """P2 §4.3: Direct 模式不得用本机 time.time() 判 lease 过期；

    以 LeaseStore.is_current（Redis PTTL 权威时钟）复核，失败即拒绝启动。
    """
    from antcode_core.application.services.lease_service import LeasePolicy, LeaseStore

    # ttl_ms=0 让 _initial_lease_wait_window_seconds 落回 fast-fail lifecycle
    # 的 0 秒窗口，失败即终止；默认 30s TTL 会让本用例真实退避重试约 28 秒。
    store = LeaseStore(redis_client=AsyncMock(), policy=LeasePolicy(ttl_ms=0))
    monkeypatch.setattr(store, "is_current", AsyncMock(return_value=False))
    transport = SimpleNamespace(
        lease_renew=AsyncMock(return_value=("lease-1", _future_expiry(), LEASE_RENEW_AFTER_MS, False)),
        _lease_store=store,
        _worker_id="worker-1",
    )
    lifecycle = _fast_fail_lifecycle()

    with pytest.raises(RuntimeError, match="权威时钟"):
        await lifecycle._initial_lease_renew(_container(transport))
    store.is_current.assert_awaited_with("worker-1", "lease-1")


def _fast_fail_lifecycle() -> Lifecycle:
    """X3: 把初始 lease 重试窗口收敛到 0，让失败用例立刻拿到最终 RuntimeError。"""
    lifecycle = Lifecycle()
    lifecycle.DEFAULT_LEASE_TTL_SECONDS = 0.0
    lifecycle.INITIAL_LEASE_WAIT_MARGIN_SECONDS = 0.0
    return lifecycle


@pytest.mark.asyncio
async def test_initial_lease_conflict_retries_until_old_lease_expires():
    """X3 回归：kill -9 后 supervisor 快速重启时旧 self lease 尚未过期，
    lease grant 返回 conflict——启动侧应退避重试直到旧 lease 过期后成功，
    而不是一击致命进 crash-loop。"""
    transport = SimpleNamespace(
        lease_renew=AsyncMock(
            side_effect=[
                RuntimeError("worker 已有有效 lease,拒绝覆盖: worker_id=worker-1"),
                RuntimeError("worker 已有有效 lease,拒绝覆盖: worker_id=worker-1"),
                ("lease-1", _future_expiry(), LEASE_RENEW_AFTER_MS, False),
            ]
        )
    )
    lifecycle = Lifecycle()
    lifecycle.INITIAL_LEASE_RETRY_INTERVAL_SECONDS = 0.01

    interval = await lifecycle._initial_lease_renew(_container(transport))

    assert interval == LEASE_RENEW_AFTER_MS // 1000
    assert transport.lease_renew.await_count == 3


@pytest.mark.asyncio
async def test_initial_lease_retry_window_is_bounded():
    """X3: 重试窗口耗尽后必须抛出最终 RuntimeError，保证启动总时长有界。"""
    transport = SimpleNamespace(
        lease_renew=AsyncMock(side_effect=RuntimeError("worker 已有有效 lease,拒绝覆盖")),
    )
    lifecycle = _fast_fail_lifecycle()

    with pytest.raises(RuntimeError, match="重试窗口耗尽"):
        await lifecycle._initial_lease_renew(_container(transport))

    assert transport.lease_renew.await_count == 1


@pytest.mark.asyncio
async def test_startup_failure_rolls_back_started_components(monkeypatch):
    monkeypatch.setattr(
        "antcode_worker.runtime.mise_bootstrap.ensure_mise",
        AsyncMock(),
    )
    transport = MagicMock(is_connected=True)
    transport.start = AsyncMock(return_value=True)
    transport.lease_renew = AsyncMock(return_value=("lease-1", 0, LEASE_RENEW_AFTER_MS, False))
    transport.deregister = AsyncMock()
    transport.stop = AsyncMock()
    runtime_manager = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    executor = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    heartbeat = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    container = _container(transport, heartbeat)
    container.runtime_manager = runtime_manager
    container.executor = executor

    with pytest.raises(RuntimeError, match="缺少有效过期时间"):
        await _fast_fail_lifecycle().startup(container)

    heartbeat.stop.assert_awaited_once()
    executor.stop.assert_awaited_once()
    runtime_manager.stop.assert_awaited_once()
    transport.deregister.assert_awaited_once_with("worker_shutdown")
    transport.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_heartbeat_reporter_fails_before_transport_start():
    transport = SimpleNamespace(start=AsyncMock())

    with pytest.raises(RuntimeError, match="heartbeat reporter"):
        await Lifecycle().startup(_container(transport))

    transport.start.assert_not_awaited()
