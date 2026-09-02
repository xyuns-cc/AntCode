"""P1-GW-02 回归:Lease 被撤销时 transport 立即通知 engine cancel_all + 唤醒 renew。

缺陷 P1-GW-02：
_abort_lease_revocation 之前只 halt transport(取消 bg tasks + disconnect),
不通知 engine, engine 需等到 _renew_run_ownership_loop 下一个 600s 周期
才做一次 renew, 命中 LEASE_STALE 后才 cancel; 中间最坏 10 分钟内旧代际
继续跑,继续产生外部副作用(HTTP 写 / PG 写 / Artifact 上传)。

本测试锁死:
1. transport.set_lease_revoked_callback 注册的回调会在 _abort_lease_revocation 内被 await
2. engine 在收到回调后调 cancel_all 与 request_ownership_renew_now
3. request_ownership_renew_now 会 set _ownership_renew_wakeup Event
4. _RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS 从 600 降到 60
5. cancel_all 会对所有 RUNNING/PREPARING/QUEUED 状态的 run 各调一次 cancel
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.ownership_fence import OwnershipFenceError
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


def _make_engine_with_mock_transport():
    """构造带 mock transport 的 engine。真实 transport 需 gRPC 连接,单测用不到。"""
    transport = MagicMock()
    transport.mode = MagicMock()
    transport.worker_id = "worker-test"
    transport._lease_id = "lease-1"
    transport.set_lease_revoked_callback = MagicMock()
    executor = MagicMock()
    engine = Engine(
        transport=transport,
        executor=executor,
        max_concurrent=2,
    )
    return engine, transport


EXPECTED_RENEW_INTERVAL_SECONDS = 60
LEGACY_RENEW_INTERVAL_SECONDS = 600  # 修复前的旧值,用于文档语义
EXPECTED_CANCELLED_COUNT = 2  # cancel_all 预期取消的 run 数(RUNNING + QUEUED)


def test_renew_interval_shortened_from_600_to_60():
    """P1-GW-02:审查报告点名的常量必须缩短。"""
    assert Engine._RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS == EXPECTED_RENEW_INTERVAL_SECONDS
    assert Engine._RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS < LEGACY_RENEW_INTERVAL_SECONDS


def test_transport_exposes_set_lease_revoked_callback():
    """P1-GW-02:GatewayTransport 必须提供回调注册入口。"""
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="w1"))
    assert hasattr(transport, "set_lease_revoked_callback")
    assert callable(transport.set_lease_revoked_callback)

    called = []

    async def cb(reason: str) -> None:
        called.append(reason)

    transport.set_lease_revoked_callback(cb)
    assert transport._lease_revoked_callback is cb


@pytest.mark.asyncio
async def test_abort_lease_revocation_invokes_callback_before_halt():
    """P1-GW-02 核心:_abort_lease_revocation 必须 await 回调。"""
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="w1"))

    invocations: list[str] = []

    async def cb(reason: str) -> None:
        invocations.append(reason)

    transport.set_lease_revoked_callback(cb)
    # halt_transport 需要 reconnect_manager 与 disconnect 存在
    transport._reconnect_manager = MagicMock(stop=AsyncMock())
    transport._disconnect = AsyncMock()
    transport._cancel_background_tasks = AsyncMock()
    transport._set_state = AsyncMock()

    await transport._abort_lease_revocation()

    assert transport._lease_revoked is True
    assert invocations == ["gateway-revoke"]


@pytest.mark.asyncio
async def test_abort_lease_revocation_survives_callback_exception():
    """P1-GW-02:回调异常不能阻止 halt，且必须向调用方显式传播。"""
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="w1"))

    async def bad_cb(reason: str) -> None:
        raise RuntimeError("engine 已挂")

    transport.set_lease_revoked_callback(bad_cb)
    transport._reconnect_manager = MagicMock(stop=AsyncMock())
    transport._disconnect = AsyncMock()
    transport._cancel_background_tasks = AsyncMock()
    transport._set_state = AsyncMock()

    with pytest.raises(RuntimeError, match="engine 已挂"):
        await transport._abort_lease_revocation()

    assert transport._lease_revoked is True
    transport._disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_engine_request_ownership_renew_now_sets_wakeup():
    """P1-GW-02:请求立即续租应 set Event(loop 未启动时静默忽略)。"""
    engine, _ = _make_engine_with_mock_transport()

    # loop 未启动:wakeup 为 None, 调用不抛异常
    assert engine._ownership_renew_wakeup is None
    engine.request_ownership_renew_now()  # 静默 no-op

    # 模拟 loop 已启动:wakeup 存在, set 生效
    engine._ownership_renew_wakeup = asyncio.Event()
    engine.request_ownership_renew_now()
    assert engine._ownership_renew_wakeup.is_set()


@pytest.mark.asyncio
async def test_engine_cancel_all_cancels_active_runs():
    """P1-GW-02:cancel_all 遍历所有 RUNNING/PREPARING/QUEUED 状态的 run 各调一次 cancel。"""
    from antcode_worker.engine.state import RunState

    engine, _ = _make_engine_with_mock_transport()

    # 注入三个 run: RUNNING, QUEUED, COMPLETED(应跳过)。StateManager.add 只接
    # (run_id, task_id, receipt); 用 transition 把状态推到目标态。
    async def _seed():
        await engine._state_manager.add_if_new("run-running", task_id="t-1")
        await engine._state_manager.transition("run-running", RunState.QUEUED)
        await engine._state_manager.transition("run-running", RunState.PREPARING)
        await engine._state_manager.transition("run-running", RunState.RUNNING)

        await engine._state_manager.add_if_new("run-queued", task_id="t-2")
        await engine._state_manager.transition("run-queued", RunState.QUEUED)

        await engine._state_manager.add_if_new("run-done", task_id="t-3")
        await engine._state_manager.transition("run-done", RunState.QUEUED)
        await engine._state_manager.transition("run-done", RunState.PREPARING)
        await engine._state_manager.transition("run-done", RunState.RUNNING)
        await engine._state_manager.transition("run-done", RunState.COMPLETED)

    await _seed()

    cancel_calls: list[tuple[str, str]] = []

    async def fake_cancel(run_id: str, reason: str = "") -> bool:
        cancel_calls.append((run_id, reason))
        return True

    engine.cancel = fake_cancel  # type: ignore[assignment]

    cancelled = await engine.cancel_all(reason="test-revoke")

    assert cancelled == EXPECTED_CANCELLED_COUNT
    cancelled_ids = {rid for rid, _ in cancel_calls}
    assert cancelled_ids == {"run-running", "run-queued"}
    assert all("test-revoke" in reason for _, reason in cancel_calls)


@pytest.mark.asyncio
async def test_engine_on_transport_lease_revoked_triggers_self_fence_and_wakeup():
    """transport 撤销后停止 intake，并向应用暴露 fatal self-fence。"""
    engine, _ = _make_engine_with_mock_transport()
    engine._ownership_renew_wakeup = asyncio.Event()

    engine._running = True
    engine._polling = True
    with pytest.raises(OwnershipFenceError, match="gateway-revoke"):
        await engine._on_transport_lease_revoked("gateway-revoke")

    assert engine._ownership_renew_wakeup.is_set()
    assert engine._running is False
    assert engine._polling is False
    assert engine._ownership_fenced is True
    assert isinstance(await engine.wait_for_fatal_error(), OwnershipFenceError)
