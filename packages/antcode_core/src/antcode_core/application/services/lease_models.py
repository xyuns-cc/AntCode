"""Immutable value types and explicit failures for Worker leases."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    """One Worker lease generation returned by the authoritative store."""

    worker_id: str
    lease_id: str
    expires_at_ms: int
    granted_at_ms: int
    sequence: int = 0


class LeaseRevokedError(RuntimeError):
    def __init__(self, worker_id: str, lease_id: str, message: str = ""):
        self.worker_id = worker_id
        self.lease_id = lease_id
        super().__init__(message or f"lease 已被撤销,拒绝再次 grant: worker_id={worker_id}")


class LeaseIneligibleError(RuntimeError):
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        super().__init__(f"Worker lifecycle does not allow leases: worker_id={worker_id}")


class LeaseConflictError(RuntimeError):
    def __init__(self, worker_id: str, current_lease_id: str, message: str = ""):
        self.worker_id = worker_id
        self.current_lease_id = current_lease_id
        super().__init__(message or f"worker 已有有效 lease,拒绝覆盖: worker_id={worker_id}")


@dataclass(frozen=True)
class LeasePolicy:
    """租约时序策略。

    ``ttl_ms`` 是**跨服务 wire 常量**，不是本地调参：Worker 的续期不变量
    ``renew_after_ms * LEASE_RENEW_SAFETY_DIVISOR < ttl_ms`` 必须用签发方
    真正生效的 TTL 校验。因此每个签发点都要把 ``ttl_ms`` 随 Lease 响应
    （``LeaseResponse.ttl_ms`` / Direct JSON 的 ``ttl_ms``）下发给 Worker；
    只要有一个签发点漏发或自行改值，Worker 就会按错误的 TTL 通过校验，
    租约在两次续期之间过期，master 补派同一个 run → 双执行。

    生产代码一律用 :func:`wire_lease_policy` 取值，不要就地 ``LeasePolicy()``；
    只有测试才允许构造非默认实例来验证边界。
    """

    ttl_ms: int = 30_000
    renew_after_ms: int = 10_000


# 所有服务端签发点共用的唯一策略实例。frozen dataclass ⇒ 共享安全，
# 且"只有一份"这件事本身就是不变量：9 个独立 ``LeasePolicy()`` 曾让
# TTL 变成隐式常量，改其中一处就会让 Worker 侧的双执行守卫失效。
_WIRE_LEASE_POLICY = LeasePolicy()


def wire_lease_policy() -> LeasePolicy:
    """返回跨服务共享的权威租约策略（Gateway / web_api / master / Worker 同源）。"""
    return _WIRE_LEASE_POLICY


__all__ = [
    "Lease",
    "LeaseConflictError",
    "LeaseIneligibleError",
    "LeasePolicy",
    "LeaseRevokedError",
    "wire_lease_policy",
]
