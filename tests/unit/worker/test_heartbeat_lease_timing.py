"""B6 回归：心跳退避不得阻塞 lease 续期。

心跳同时是 lease 续期信号。只要续期节拍慢过租约 TTL，master 就会判定
Worker 死亡并把同一个 run 补派给另一个 Worker（双执行 + 外部副作用双写）。
这里用虚拟时钟驱动真实的 ``HeartbeatReporter._loop``，不 mock 被测逻辑本身。
"""

import asyncio
from collections.abc import Callable

import pytest
from antcode_worker.heartbeat.lease_timing import (
    HeartbeatClock,
    LeaseRenewalWindow,
    LeaseRenewIntervalError,
    reconnect_delay_seconds,
)
from antcode_worker.heartbeat.reporter import HeartbeatReporter, HeartbeatState

LEASE_TTL_MS = 30_000
SERVER_RENEW_AFTER_MS = 10_000
FASTER_RENEW_AFTER_MS = 4_000
# 恰好等于 TTL/2：不变量要求"严格小于"，因此必须报错。
UNSAFE_RENEW_AFTER_MS = 15_000
MILLIS = 1000
MAX_ATTEMPT_GAP_SECONDS = LEASE_TTL_MS / (MILLIS * 2)
SERVER_INTERVAL_SECONDS = SERVER_RENEW_AFTER_MS / MILLIS
FASTER_INTERVAL_SECONDS = FASTER_RENEW_AFTER_MS / MILLIS

# 虚拟秒数需覆盖 RECONNECT_BACKOFF_MAX(300s)，才能证明"最大退避"下续期照常。
FAILURE_CYCLES = 900
SHORT_CYCLES = 3
ADOPT_ON_CALL = 2
FIRST = 0
SECOND = 1


class _VirtualClock:
    """虚拟时钟：记录 sleep 时长并推进单调时间，不真实等待。"""

    def __init__(self, max_sleeps: int, on_exhausted: Callable[[], None]) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self._max_sleeps = max_sleeps
        self._on_exhausted = on_exhausted

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        if len(self.sleeps) >= self._max_sleeps:
            self._on_exhausted()
        await asyncio.sleep(0)  # 让后台重连任务有机会被调度

    def monotonic(self) -> float:
        return self.now


class _StubTransport:
    """可控传输层：只决定"续期成功与否"和"重连成功与否"。"""

    def __init__(self, *, renew_ok: bool, reconnect_ok: bool = False) -> None:
        self.connected = True
        self.renew_ok = renew_ok
        self.reconnect_ok = reconnect_ok
        self.renew_calls = 0
        self.reconnect_calls = 0
        self.reconnect_gate: asyncio.Event | None = None

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def lease_renew_after_ms(self) -> int | None:
        """本桩不模拟服务端下发节拍；节拍由用例自己 apply。"""
        return None

    async def send_heartbeat(self, heartbeat) -> bool:
        self.renew_calls += 1
        return self.renew_ok

    async def reconnect(self) -> bool:
        self.reconnect_calls += 1
        if self.reconnect_gate is not None:
            await self.reconnect_gate.wait()
        return self.reconnect_ok


def _build(transport: _StubTransport, cycles: int) -> tuple[HeartbeatReporter, _VirtualClock]:
    window = LeaseRenewalWindow(ttl_ms=LEASE_TTL_MS, renew_after_ms=SERVER_RENEW_AFTER_MS)
    holder: dict[str, HeartbeatReporter] = {}
    clock = _VirtualClock(cycles, lambda: setattr(holder["reporter"], "_running", False))
    reporter = HeartbeatReporter(
        transport=transport,
        worker_id="worker-1",
        renewal_window=window,
        clock=HeartbeatClock(sleep=clock.sleep, monotonic=clock.monotonic),
    )
    holder["reporter"] = reporter
    reporter._running = True
    return reporter, clock


@pytest.mark.asyncio
async def test_max_backoff_never_starves_lease_renewal() -> None:
    """连续失败进入最大重连退避后，续期尝试间隔仍 < TTL/2。"""
    transport = _StubTransport(renew_ok=False)
    reporter, clock = _build(transport, FAILURE_CYCLES)

    await reporter._loop()

    # 退避确实已经打满（否则这条断言证明不了 B6 的场景）
    assert (
        reconnect_delay_seconds(
            reporter.reconnect_attempts,
            reporter.RECONNECT_BACKOFF_BASE,
            reporter.RECONNECT_BACKOFF_MAX,
        )
        == reporter.RECONNECT_BACKOFF_MAX
    )
    assert reporter.state is HeartbeatState.DEGRADED
    assert max(clock.sleeps) < MAX_ATTEMPT_GAP_SECONDS
    await reporter.stop()
    # 每个周期都真的尝试了续期，没有被退避吞掉
    assert transport.renew_calls == len(clock.sleeps) == FAILURE_CYCLES


@pytest.mark.asyncio
async def test_slow_reconnect_does_not_block_renewal() -> None:
    """重连挂起在后台时，主循环照常按节拍续期。"""
    transport = _StubTransport(renew_ok=False, reconnect_ok=True)
    transport.reconnect_gate = asyncio.Event()
    reporter, clock = _build(transport, FAILURE_CYCLES)

    await reporter._loop()

    assert transport.reconnect_calls == 1  # 仍卡在 gate 上
    assert transport.renew_calls == FAILURE_CYCLES
    assert max(clock.sleeps) < MAX_ATTEMPT_GAP_SECONDS

    transport.reconnect_gate.set()
    await reporter.stop()


@pytest.mark.asyncio
async def test_server_renew_after_ms_is_adopted_by_next_cycle() -> None:
    """服务端返回的 renew_after_ms 立即驱动后续周期的节拍。"""
    transport = _StubTransport(renew_ok=True)
    reporter, clock = _build(transport, SHORT_CYCLES)

    original_send = transport.send_heartbeat

    async def send_and_push_server_interval(heartbeat) -> bool:
        result = await original_send(heartbeat)
        if transport.renew_calls == ADOPT_ON_CALL:
            reporter.apply_lease_renew_after_ms(FASTER_RENEW_AFTER_MS)
        return result

    transport.send_heartbeat = send_and_push_server_interval  # type: ignore[method-assign]

    await reporter._loop()
    await reporter.stop()

    assert clock.sleeps[FIRST] == SERVER_INTERVAL_SECONDS
    assert clock.sleeps[SECOND] == FASTER_INTERVAL_SECONDS
    assert reporter.interval == FASTER_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_degraded_state_keeps_full_renewal_cadence() -> None:
    """降级状态不得放慢续期（DEGRADED_INTERVAL 覆盖路径已删除）。"""
    transport = _StubTransport(renew_ok=True)
    reporter, _clock = _build(transport, SHORT_CYCLES)
    reporter._state = HeartbeatState.DEGRADED

    assert not hasattr(HeartbeatReporter, "DEGRADED_INTERVAL")
    assert reporter._next_delay_seconds(True) == SERVER_INTERVAL_SECONDS
    assert reporter._next_delay_seconds(False) < SERVER_INTERVAL_SECONDS
    assert reporter._next_delay_seconds(False) < MAX_ATTEMPT_GAP_SECONDS


def test_unsafe_window_is_rejected_at_construction() -> None:
    """interval >= TTL/2 直接抛错，不静默 clamp。"""
    with pytest.raises(LeaseRenewIntervalError):
        LeaseRenewalWindow(ttl_ms=LEASE_TTL_MS, renew_after_ms=UNSAFE_RENEW_AFTER_MS)


@pytest.mark.asyncio
async def test_start_rejects_interval_at_or_above_half_ttl() -> None:
    """启动时校验不变量：违反即失败，绝不带病启动。"""
    transport = _StubTransport(renew_ok=True)
    reporter, _clock = _build(transport, SHORT_CYCLES)
    reporter._running = False

    with pytest.raises(LeaseRenewIntervalError):
        await reporter.start(interval=UNSAFE_RENEW_AFTER_MS // MILLIS)

    assert reporter.is_running is False
    assert reporter.interval == SERVER_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_unsafe_server_interval_is_rejected_not_clamped() -> None:
    """服务端下发越界节拍时显式失败，并保留上一份有效节拍。"""
    transport = _StubTransport(renew_ok=True)
    reporter, _clock = _build(transport, SHORT_CYCLES)

    with pytest.raises(LeaseRenewIntervalError):
        reporter.apply_lease_renew_after_ms(UNSAFE_RENEW_AFTER_MS)

    assert reporter.renewal_window.renew_after_ms == SERVER_RENEW_AFTER_MS


class _CadenceTransport:
    """服务端时序可变的传输层桩，用于驱动续期循环在运行期失败。"""

    def __init__(self) -> None:
        self.connected = True
        self.lease_ttl_ms: int | None = None
        self.lease_renew_after_ms: int | None = None

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def send_heartbeat(self, heartbeat) -> bool:
        return True

    async def reconnect(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_renewal_loop_death_triggers_fatal_channel_and_stops_lying() -> None:
    """续期循环终止必须触发致命通道，且 is_running 不得继续撒谎。

    B6 把"服务端下发会掉租约的节拍"改成显式抛错，但异常抛进一个没人监听的
    Task 等于 fail-silent：_running 仍为 True、Worker 照常执行任务，而租约已
    停止续期 → master 补派同一个 run → 双执行。这正是 B6 要消灭的场景。
    """
    transport = _CadenceTransport()
    reporter = HeartbeatReporter(transport, "w-fatal")
    recorded: list[BaseException] = []
    reporter.set_fatal_error_handler(recorded.append)

    await reporter.start(interval=1)
    assert reporter.is_running is True

    # 服务端改口下发一个违反 renew*2 < ttl 的节拍：循环必须炸穿而不是继续跑
    transport.lease_ttl_ms = 8_000
    transport.lease_renew_after_ms = 5_000
    task = reporter._task
    assert task is not None
    with pytest.raises(LeaseRenewIntervalError):
        await task

    assert recorded, "续期循环终止后未上报致命错误——防线建在了无人接住的异常上"
    assert isinstance(recorded[FIRST], LeaseRenewIntervalError)
    assert reporter.is_running is False, "循环已死但 is_running 仍返回 True"
    assert reporter.state is HeartbeatState.STOPPED
