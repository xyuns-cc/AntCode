"""租约 TTL 必须由服务端权威下发，不能由 Worker 按本地默认值假定。

上一轮修复用 ``LeaseRenewalWindow`` 把不变量 ``renew_after_ms * 2 < ttl_ms``
做成"构造即校验"，但 ``ttl_ms`` 是 Worker 自己按 ``LeasePolicy()`` 默认值
（30s）假定的——``LeaseResponse`` 根本没有这个字段。于是守卫**结构性失效**：
服务端下发 TTL=8s + renew=5s 时，``5000*2=10000 < 30000`` 校验通过，而实际
``10000 >= 8000`` 已经违反不变量，租约会在两次续期之间过期，master 补派同一个
run → 双执行。

这里用**真实的** ``GatewayTransport`` / ``RedisTransport`` + 真实的
``HeartbeatReporter._loop``（虚拟时钟驱动，不真实等待）验证 TTL 贯通，
只把最外层的 gRPC stub / Direct HTTP 控制面换成脚本化桩。
"""

from __future__ import annotations

import ast
import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from antcode_contracts import control_pb2
from antcode_core.application.services.lease_service import LeaseStore, wire_lease_policy
from antcode_worker.heartbeat.lease_timing import (
    DEFAULT_LEASE_TTL_MS,
    HeartbeatClock,
    LeaseRenewalWindow,
    LeaseRenewIntervalError,
    LeaseTtlUnavailableError,
)
from antcode_worker.heartbeat.reporter import HeartbeatReporter
from antcode_worker.transport.base import TransportBase, WorkerState
from antcode_worker.transport.gateway.subscriptions import SUBSCRIPTION_NAMES
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport
from antcode_worker.transport.redis.direct_control import DirectLeaseGrant
from antcode_worker.transport.redis.transport import RedisTransport

LEASE_ID = "lease-1"
MILLIS = 1000

# 服务端收紧后的 TTL，远小于 Worker 本地默认的 30s。
SERVER_TTL_MS = 8_000
SAFE_RENEW_AFTER_MS = 3_000
# 8s TTL 下越界（3000*2 < 8000 安全，5000*2 >= 8000 越界），
# 但按本地默认 30s 校验时 5000*2 < 30000 会静默通过——正是本次要闭合的缺口。
UNSAFE_UNDER_SERVER_TTL_MS = 5_000
# proto3 / JSON 标量无 presence：0 == 服务端本次未下发该字段。
UNSET_TTL_MS = 0

STARTUP_TTL_MS = 30_000
STARTUP_RENEW_AFTER_MS = 10_000
STARTUP_INTERVAL_SECONDS = STARTUP_RENEW_AFTER_MS / MILLIS
SAFE_INTERVAL_SECONDS = SAFE_RENEW_AFTER_MS / MILLIS
SERVER_MAX_ATTEMPT_GAP_SECONDS = SERVER_TTL_MS / (MILLIS * 2)

TWO_CYCLES = 2

PRODUCTION_ROOTS = (Path("packages"), Path("services"))
# 唯一允许出现裸 ``LeasePolicy()`` 的地方：工厂自己。
POLICY_FACTORY_FILE = Path("packages/antcode_core/src/antcode_core/application/services/lease_models.py")


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


def _gateway_transport(ttl_ms: int, renew_after_ms: int) -> GatewayTransport:
    """真实 GatewayTransport，只把 ControlService stub 换成脚本化桩。"""

    async def lease(request: Any, metadata: Any = None) -> Any:
        return control_pb2.LeaseResponse(
            lease_id=LEASE_ID,
            expires_at_ms=int(time.time() * MILLIS) + ttl_ms,
            renew_after_ms=renew_after_ms,
            ttl_ms=ttl_ms,
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


def _direct_transport(ttl_ms: int, renew_after_ms: int) -> RedisTransport:
    """真实 RedisTransport（Direct），只把 HTTP 控制面换成脚本化桩。"""

    async def lease_renew(
        current_lease_id: str,
        metrics: dict | None,
        capabilities: dict[str, str],
    ) -> DirectLeaseGrant:
        return DirectLeaseGrant(
            lease_id=LEASE_ID,
            expires_at_ms=int(time.time() * MILLIS) + ttl_ms,
            renew_after_ms=renew_after_ms,
            ttl_ms=ttl_ms,
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
    """启动引导窗口刻意用本地默认 30s，好证明服务端 TTL 真的替换了它。"""
    holder: dict[str, HeartbeatReporter] = {}
    clock = _VirtualClock(cycles, lambda: setattr(holder["reporter"], "_running", False))
    reporter = HeartbeatReporter(
        transport=transport,
        worker_id="worker-1",
        renewal_window=LeaseRenewalWindow(ttl_ms=STARTUP_TTL_MS, renew_after_ms=STARTUP_RENEW_AFTER_MS),
        clock=HeartbeatClock(sleep=clock.sleep, monotonic=clock.monotonic),
    )
    holder["reporter"] = reporter
    reporter._running = True
    return reporter, clock


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_transport_exposes_server_lease_ttl(make_transport) -> None:
    """两个实现都把服务端 ttl_ms 缓存在只读属性上，而不是丢弃。"""
    transport = make_transport(SERVER_TTL_MS, SAFE_RENEW_AFTER_MS)

    assert transport.lease_ttl_ms is None
    await transport.lease_renew(LEASE_ID)

    assert transport.lease_ttl_ms == SERVER_TTL_MS
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_server_ttl_replaces_local_default_in_renewal_window(make_transport) -> None:
    """续期窗口按服务端 ttl_ms 重建，本地默认 30s 不再参与不变量校验。"""
    transport = make_transport(SERVER_TTL_MS, SAFE_RENEW_AFTER_MS)
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    await reporter._loop()

    assert reporter.renewal_window.ttl_ms == SERVER_TTL_MS
    assert reporter.renewal_window.ttl_ms != DEFAULT_LEASE_TTL_MS
    assert reporter.renewal_window.renew_after_ms == SAFE_RENEW_AFTER_MS
    # 单次续期尝试的上限也跟着服务端 TTL 收紧（TTL/2），不再是本地 15s。
    assert reporter.renewal_window.max_attempt_gap_seconds == SERVER_MAX_ATTEMPT_GAP_SECONDS
    assert clock.sleeps == [SAFE_INTERVAL_SECONDS] * TWO_CYCLES
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_missing_server_ttl_fails_loudly(make_transport) -> None:
    """服务端签发了租约却不下发 ttl_ms 时显式失败，不回落到本地默认 TTL。"""
    transport = make_transport(UNSET_TTL_MS, SAFE_RENEW_AFTER_MS)
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    with pytest.raises(LeaseTtlUnavailableError):
        await reporter._loop()

    assert transport.lease_ttl_ms is None
    assert transport.lease_renew_after_ms == SAFE_RENEW_AFTER_MS
    assert reporter.renewal_window.ttl_ms == STARTUP_TTL_MS
    assert clock.sleeps == []
    await _shutdown(transport)


@pytest.mark.asyncio
@TRANSPORT_FACTORIES
async def test_renew_violating_server_ttl_is_rejected_not_silently_passed(make_transport) -> None:
    """服务端 TTL 下越界的节拍必须炸穿主循环，且旧节拍未被静默替换。

    这组值正是缺口的复现：按本地默认 30s 校验 ``5000*2 < 30000`` 会通过。
    """
    assert UNSAFE_UNDER_SERVER_TTL_MS * 2 < DEFAULT_LEASE_TTL_MS  # 旧实现会放行
    assert UNSAFE_UNDER_SERVER_TTL_MS * 2 >= SERVER_TTL_MS  # 服务端 TTL 下确实越界

    transport = make_transport(SERVER_TTL_MS, UNSAFE_UNDER_SERVER_TTL_MS)
    reporter, clock = _build_reporter(transport, TWO_CYCLES)

    with pytest.raises(LeaseRenewIntervalError):
        await reporter._loop()

    assert reporter.renewal_window.ttl_ms == STARTUP_TTL_MS
    assert reporter.renewal_window.renew_after_ms == STARTUP_RENEW_AFTER_MS
    assert reporter.interval == STARTUP_INTERVAL_SECONDS
    assert clock.sleeps == []
    await _shutdown(transport)


def test_shared_factory_is_the_single_policy_source() -> None:
    """工厂返回同一个 frozen 实例，LeaseStore 默认策略也走它。"""
    policy = wire_lease_policy()

    assert wire_lease_policy() is policy
    assert LeaseStore(redis_client=object(), namespace="test").policy is policy


def _constructs_lease_policy(source: str) -> bool:
    """源码里是否存在真正的 ``LeasePolicy(...)`` 调用（注释/文档串不算）。"""
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "LeasePolicy"
        for node in ast.walk(ast.parse(source))
    )


def test_no_production_module_constructs_its_own_lease_policy() -> None:
    """生产代码不得就地构造 ``LeasePolicy``：9 个独立构造点正是 TTL 漂移的根因。"""
    offenders = [
        str(path)
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if path != POLICY_FACTORY_FILE and _constructs_lease_policy(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_both_server_paths_publish_the_shared_ttl() -> None:
    """Gateway 与 Direct 两条签发路径下发同一个权威 ttl_ms。"""
    from antcode_gateway.services.lease_heartbeat import revoked_lease_response
    from antcode_web_api.routes.v1.workers_direct_lease import _revoked_response

    policy = wire_lease_policy()
    grpc_response = revoked_lease_response(policy, "revoked")
    direct_response = _revoked_response(LeaseStore(redis_client=object(), namespace="test"))

    assert grpc_response.ttl_ms == policy.ttl_ms
    assert direct_response.data["ttl_ms"] == policy.ttl_ms
