"""租约续期时序（B6）。

Worker 心跳同时承担 lease 续期职责：一旦续期节拍慢于租约 TTL，master 会
判定 Worker 死亡并把同一个 run 补派给另一个 Worker，造成双执行与外部副作用
双写。本模块把"续期节拍"收敛成不可变、构造即校验的值对象：任何违反
``interval < TTL / LEASE_RENEW_SAFETY_DIVISOR`` 的配置都直接抛错，
绝不静默 clamp 后继续跑——静默 clamp 会让 Worker 以为自己还持有租约。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from antcode_core.application.services.lease_models import wire_lease_policy

from antcode_worker.transport.lease_cadence import (
    LEASE_RENEW_SAFETY_DIVISOR as LEASE_RENEW_SAFETY_DIVISOR,
)

MILLISECONDS_PER_SECOND = 1000

# 服务端 Lease 契约允许的最小续期周期，与 app/lifecycle.py 的启动校验同源。
MIN_RENEW_AFTER_MS = MILLISECONDS_PER_SECOND

# 取租约还没签发时的引导值，来源是跨服务共享策略（避免 Worker 侧另抄一份）。
# 注意：这**只是引导值**。租约一旦签发，TTL 必须由服务端 ``LeaseResponse.ttl_ms``
# 提供——本地默认值与服务端真实 TTL 不一致时，用它校验不变量会静默放行。
_BOOTSTRAP_LEASE_POLICY = wire_lease_policy()
DEFAULT_LEASE_TTL_MS = _BOOTSTRAP_LEASE_POLICY.ttl_ms
DEFAULT_RENEW_AFTER_MS = _BOOTSTRAP_LEASE_POLICY.renew_after_ms


class LeaseRenewalWindowError(RuntimeError):
    """续期时序不变量被破坏；任何来源（启动参数 / 服务端下发）都用它显式失败。"""


class LeaseRenewIntervalError(LeaseRenewalWindowError):
    """续期节拍违反 ``interval < TTL / LEASE_RENEW_SAFETY_DIVISOR`` 不变量。"""

    def __init__(self, renew_after_ms: int, ttl_ms: int) -> None:
        self.renew_after_ms = renew_after_ms
        self.ttl_ms = ttl_ms
        super().__init__(
            f"lease 续期间隔必须严格小于 TTL/{LEASE_RENEW_SAFETY_DIVISOR}: "
            f"renew_after_ms={renew_after_ms} ttl_ms={ttl_ms}"
        )


class LeaseTtlUnavailableError(LeaseRenewalWindowError):
    """服务端签发了租约却没下发 ``ttl_ms``，不变量无从校验。

    绝不回落到本地 ``LeasePolicy()`` 默认值：服务端 TTL 一旦小于本地假定值
    （哪怕只是某个签发点漏改造成的不一致），``renew*2 < ttl`` 会用错误的 TTL
    静默通过，租约在两次续期之间过期，master 补派同一个 run → 双执行。
    """

    def __init__(self, renew_after_ms: int) -> None:
        self.renew_after_ms = renew_after_ms
        super().__init__(
            "服务端 Lease 响应缺少权威 ttl_ms，无法校验续期不变量（拒绝回落到本地默认 TTL）: "
            f"renew_after_ms={renew_after_ms}"
        )


@dataclass(frozen=True)
class LeaseRenewalWindow:
    """续期时序的不可变快照，构造即校验不变量。

    ``ttl_ms`` 的默认值只服务于"租约尚未签发"的引导阶段。租约签发后
    ``HeartbeatReporter`` 一律用服务端下发的 ``LeaseResponse.ttl_ms``
    重建本对象（见 ``adopt_server_window``）——否则服务端把 TTL 收到
    8s 时，本地按 30s 校验 ``5000*2 < 30000`` 会通过，而实际
    ``10000 >= 8000`` 已经违反不变量，租约会在两次续期之间过期。
    """

    ttl_ms: int = DEFAULT_LEASE_TTL_MS
    renew_after_ms: int = DEFAULT_RENEW_AFTER_MS

    def __post_init__(self) -> None:
        if self.ttl_ms < MIN_RENEW_AFTER_MS * LEASE_RENEW_SAFETY_DIVISOR:
            raise LeaseRenewalWindowError(f"lease ttl_ms 过小，无法安全续期: ttl_ms={self.ttl_ms}")
        if self.renew_after_ms < MIN_RENEW_AFTER_MS:
            raise LeaseRenewalWindowError(
                f"lease renew_after_ms 必须至少为 {MIN_RENEW_AFTER_MS}: {self.renew_after_ms}"
            )
        if self.renew_after_ms * LEASE_RENEW_SAFETY_DIVISOR >= self.ttl_ms:
            raise LeaseRenewIntervalError(self.renew_after_ms, self.ttl_ms)

    @property
    def interval_seconds(self) -> float:
        """两次续期尝试之间的等待时长（服务端 renew_after_ms 驱动）。"""
        return self.renew_after_ms / MILLISECONDS_PER_SECOND

    @property
    def max_attempt_gap_seconds(self) -> float:
        """单次续期尝试允许占用的最长时间，等于 TTL/2。

        等待 interval(< TTL/2) + 单次尝试(<= TTL/2) < TTL，
        因此任意两次续期尝试的间隔恒小于租约 TTL。
        """
        return self.ttl_ms / (MILLISECONDS_PER_SECOND * LEASE_RENEW_SAFETY_DIVISOR)

    def with_renew_after_ms(self, renew_after_ms: int) -> LeaseRenewalWindow:
        """按服务端返回值派生新节拍；违反不变量直接抛错。"""
        return LeaseRenewalWindow(ttl_ms=self.ttl_ms, renew_after_ms=renew_after_ms)


def server_lease_window(ttl_ms: int | None, renew_after_ms: int) -> LeaseRenewalWindow:
    """按服务端下发的权威时序构造续期窗口。

    ``ttl_ms is None`` 表示服务端签发了租约却没下发权威 TTL：直接抛
    ``LeaseTtlUnavailableError``，**不**回落到本地 ``LeasePolicy()`` 默认值。
    回落会让 ``renew*2 < ttl`` 用错误的 TTL 静默通过（服务端 TTL=8s +
    renew=5s 时 ``5000*2 < 30000`` 成立，而实际 ``10000 >= 8000``），
    租约在两次续期之间过期，master 补派同一个 run → 双执行。
    """
    if ttl_ms is None:
        raise LeaseTtlUnavailableError(renew_after_ms)
    return LeaseRenewalWindow(ttl_ms=ttl_ms, renew_after_ms=renew_after_ms)


@runtime_checkable
class LeaseRenewSource(Protocol):
    """传输层回传"服务端权威租约时序"的契约。

    ``TransportBase`` 已实现本协议，因此 Gateway / Direct 两个实现的行为
    完全一致：谁都不会把服务端下发的 ``ttl_ms`` / ``renew_after_ms``
    丢在传输层里。两个值必须成对读回——只有服务端权威 TTL 才能校验
    ``renew_after_ms * LEASE_RENEW_SAFETY_DIVISOR < ttl_ms``。
    """

    @property
    def lease_renew_after_ms(self) -> int | None:
        """最近一次 Lease 响应里的 ``renew_after_ms``；``None`` = 服务端尚未下发。"""
        ...

    @property
    def lease_ttl_ms(self) -> int | None:
        """最近一次 Lease 响应里的权威 ``ttl_ms``；``None`` = 服务端尚未下发。"""
        ...


def adopt_server_window(source: LeaseRenewSource) -> LeaseRenewalWindow | None:
    """按传输层缓存的服务端时序构造续期窗口。

    返回 ``None`` 表示服务端尚未签发过租约，调用方沿用启动引导值；
    已签发但缺 ``ttl_ms`` 或越界时由 ``server_lease_window`` 显式抛错。
    """
    renew_after_ms = source.lease_renew_after_ms
    if renew_after_ms is None:
        return None
    return server_lease_window(source.lease_ttl_ms, renew_after_ms)


@dataclass(frozen=True)
class HeartbeatClock:
    """可注入时钟：让退避与续期节拍在测试中可断言，无需真实等待。"""

    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    monotonic: Callable[[], float] = time.monotonic


def reconnect_delay_seconds(attempts: int, base: float, maximum: float) -> float:
    """重连退避时长。

    只用于推迟"下一次重连尝试"，绝不参与续期节拍计算——B6 的根因正是
    退避被 await 在心跳主循环里，把续期一起堵死了。
    """
    return min(base**attempts, maximum)


__all__ = [
    "DEFAULT_LEASE_TTL_MS",
    "adopt_server_window",
    "DEFAULT_RENEW_AFTER_MS",
    "HeartbeatClock",
    "LEASE_RENEW_SAFETY_DIVISOR",
    "LeaseRenewIntervalError",
    "LeaseRenewSource",
    "LeaseRenewalWindow",
    "LeaseRenewalWindowError",
    "LeaseTtlUnavailableError",
    "MILLISECONDS_PER_SECOND",
    "MIN_RENEW_AFTER_MS",
    "reconnect_delay_seconds",
    "server_lease_window",
]
