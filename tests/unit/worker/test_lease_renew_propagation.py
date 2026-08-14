"""服务端 ``renew_after_ms`` 从 transport 回推到 ``HeartbeatReporter``。

``send_heartbeat`` 只返回 ``bool``，服务端在 Lease 响应里下发的续期节拍原本
被丢在传输层，运行期节拍永远等于启动协商值——服务端中途收紧 TTL 时 Worker
不会跟进，租约到期后 master 会把同一个 run 补派给别人（双执行）。

这里用**真实的** ``GatewayTransport`` / ``RedisTransport`` + 真实的
``HeartbeatReporter._loop``（虚拟时钟驱动，不真实等待）验证回推链路，
只把最外层的 gRPC stub / Direct HTTP 控制面换成脚本化桩。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from antcode_contracts import control_pb2
from antcode_worker.heartbeat.lease_timing import (
    HeartbeatClock,
    LeaseRenewalWindow,
    LeaseRenewIntervalError,
)
from antcode_worker.heartbeat.reporter import HeartbeatReporter
from antcode_worker.transport.base import TransportBase, WorkerState
from antcode_worker.transport.gateway.subscriptions import SUBSCRIPTION_NAMES
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport
from antcode_worker.transport.redis.direct_control import DirectLeaseGrant
from antcode_worker.transport.redis.transport import RedisTransport

LEASE_ID = "lease-1"
LEASE_TTL_MS = 30_000
STARTUP_RENEW_AFTER_MS = 10_000
FASTER_RENEW_AFTER_MS = 4_000
# 恰好等于 TTL/2：不变量要求"严格小于"，服务端下发它必须显式报错。
UNSAFE_RENEW_AFTER_MS = 15_000
# proto3 / JSON 标量无 presence：0 == 服务端本次未下发该字段。
UNSET_RENEW_AFTER_MS = 0

MILLIS = 1000
STARTUP_INTERVAL_SECONDS = STARTUP_RENEW_AFTER_MS / MILLIS
FASTER_INTERVAL_SECONDS = FASTER_RENEW_AFTER_MS / MILLIS
TWO_CYCLES = 2
FIRST = 0
SECOND = 1


class _VirtualClock:
    """虚拟时钟：记录 sleep 时长并推进单调时间，跑满 N 拍后停掉主循环。"""

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
        await asyncio.sleep(0)

    def monotonic(self) -> float:
        return self.now


def _scripted(values: list[int]) -> Callable[[], int]:
    """按调用次序吐出服务端 ``renew_after_ms``，末值重复。"""
    calls = {"n": 0}

    def next_value() -> int:
        index = min(calls["n"], len(values) - 1)
        calls["n"] += 1
        return values[index]

    return next_value


def _gateway_transport(renew_after_ms_script: list[int]) -> GatewayTransport:
    """真实 GatewayTransport，只把 ControlService stub 换成脚本化桩。"""
    next_renew = _scripted(renew_after_ms_script)

    async def lease(request: Any, metadata: Any = None) -> Any:
        return control_pb2.LeaseResponse(
            lease_id=LEASE_ID,
            expires_at_ms=int(time.time() * MILLIS) + LEASE_TTL_MS,
            renew_after_ms=next_renew(),
            ttl_ms=LEASE_TTL_MS,
        )

    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._connected = True
    transport._channel = object()
    transport._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, True)
    transport._control_stub = SimpleNamespace(Lease=lease)
    transport._lease_id = LEASE_ID
    # 订阅循环需要真实 gRPC，测试里用永挂起的占位任务顶掉 _start_subscriptions。
    transport._task_subscriber = asyncio.create_task(asyncio.Event().wait())
    transport._control_subscriber = asyncio.create_task(asyncio.Event().wait())
    return transport


def _direct_transport(renew_after_ms_script: list[int]) -> RedisTransport:
    """真实 RedisTransport（Direct），只把 HTTP 控制面换成脚本化桩。"""
    next_renew = _scripted(renew_after_ms_script)

    async def lease_renew(
        current_lease_id: str,
        metrics: dict | None,
        capabilities: dict[str, str],
    ) -> DirectLeaseGrant:
        return DirectLeaseGrant(
            lease_id=LEASE_ID,
            expires_at_ms=int(time.time() * MILLIS) + LEASE_TTL_MS,
            renew_after_ms=next_renew(),
            ttl_ms=LEASE_TTL_MS,
            revoked=False,
        )

    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=SimpleNamespace(lease_renew=lease_renew),
    )
    transport._running = True
    transport._state = WorkerState.ONLINE
    transport._lease_store = SimpleNamespace()
    transport._lease_id = LEASE_ID
    return transport


async def _shutdown(transport: TransportBase) -> None:
    for task in (
        getattr(transport, "_task_subscriber", None),
        getattr(transport, "_control_subscriber", None),
    ):
        if isinstance(task, asyncio.Task):
            task.cancel()


TRANSPORT_FACTORIES = pytest.mark.parametrize(
    "make_transport",
    [pytest.param(_gateway_transport, id="gateway"), pytest.param(_direct_transport, id="direct")],
)


def _build_reporter(transport: TransportBase, cycles: int) -> tuple[HeartbeatReporter, _VirtualClock]:
    holder: dict[str, HeartbeatReporter] = {}
    clock = _VirtualClock(cycles, lambda: setattr(holder["reporter"], "_running", False))
    reporter = HeartbeatReporter(
        transport=transport,
        worker_id="worker-1",
        renewal_window=LeaseRenewalWindow(ttl_ms=LEASE_TTL_MS, renew_after_ms=STARTUP_RENEW_AFTER_MS),
        clock=HeartbeatClock(sleep=clock.sleep, monotonic=clock.monotonic),
    )
    holder["reporter"] = reporter
    reporter._running = True
    return reporter, clock


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_transport_exposes_server_lease_cadence(make_transport) -> None:
    """两个实现都把服务端 renew_after_ms 缓存在只读属性上，而不是丢弃。"""
    transport = make_transport([FASTER_RENEW_AFTER_MS])

    assert transport.lease_renew_after_ms is None
    await transport.lease_renew(LEASE_ID)

    assert transport.lease_renew_after_ms == FASTER_RENEW_AFTER_MS
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_server_cadence_change_takes_effect_next_cycle(make_transport) -> None:
    """服务端中途收紧节拍后，下一拍的等待时长立刻跟着变。"""
    transport = make_transport([STARTUP_RENEW_AFTER_MS, FASTER_RENEW_AFTER_MS])
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    await reporter._loop()

    assert clock.sleeps[FIRST] == STARTUP_INTERVAL_SECONDS
    assert clock.sleeps[SECOND] == FASTER_INTERVAL_SECONDS
    assert reporter.interval == FASTER_INTERVAL_SECONDS
    assert reporter.renewal_window.renew_after_ms == FASTER_RENEW_AFTER_MS
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_unsafe_server_cadence_fails_loudly(make_transport) -> None:
    """服务端下发 >= TTL/2 的节拍时炸穿主循环，且旧节拍未被静默替换。"""
    transport = make_transport([UNSAFE_RENEW_AFTER_MS])
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    with pytest.raises(LeaseRenewIntervalError):
        await reporter._loop()

    assert transport.lease_renew_after_ms == UNSAFE_RENEW_AFTER_MS
    assert reporter.renewal_window.renew_after_ms == STARTUP_RENEW_AFTER_MS
    assert reporter.interval == STARTUP_INTERVAL_SECONDS
    assert clock.sleeps == []
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_absent_server_cadence_keeps_current_pace(make_transport) -> None:
    """服务端不下发该字段时沿用当前节拍，且不报错。"""
    transport = make_transport([UNSET_RENEW_AFTER_MS])
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    await reporter._loop()

    assert transport.lease_renew_after_ms is None
    assert reporter.renewal_window.renew_after_ms == STARTUP_RENEW_AFTER_MS
    assert clock.sleeps == [STARTUP_INTERVAL_SECONDS] * TWO_CYCLES
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_absent_cadence_after_a_known_one_keeps_the_authoritative_value(make_transport) -> None:
    """先下发过节拍、后续响应不带该字段时，保持已知的服务端权威值。"""
    transport = make_transport([FASTER_RENEW_AFTER_MS, UNSET_RENEW_AFTER_MS])
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    await reporter._loop()

    assert transport.lease_renew_after_ms == FASTER_RENEW_AFTER_MS
    assert clock.sleeps == [FASTER_INTERVAL_SECONDS] * TWO_CYCLES
    await _shutdown(transport)
