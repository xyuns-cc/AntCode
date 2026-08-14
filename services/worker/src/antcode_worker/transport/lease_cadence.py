"""服务端权威的 Lease 时序（TTL + 续期节拍）。

``send_heartbeat`` 只返回 ``bool``，服务端在 Lease 响应里下发的
``renew_after_ms`` 原本被丢在传输层，运行期节拍永远停在启动协商值：服务端
中途收紧 TTL 时 Worker 不会跟进，租约到期后 master 会把同一个 run 补派给
别人（双执行）。本模块把"解码 + 持有"这条链路收敛到一处，Gateway 与 Direct
两个实现共用，``HeartbeatReporter`` 每个续期周期读回。

``ttl_ms`` 与 ``renew_after_ms`` 是**一对**：续期不变量
``renew_after_ms * 2 < ttl_ms`` 只有用服务端真正生效的 TTL 校验才成立。
本模块只负责缓存服务端下发值，**不做校验也不抛错**——传输层的 lease 调用
被上层宽异常捕获，在这里抛错等于把故障吞成一次"续期失败"。校验与显式失败
由 ``HeartbeatReporter`` 统一执行（``LeaseRenewalWindow``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# proto3 标量与 Direct 的 JSON 控制面都没有 presence 语义：``ttl_ms`` /
# ``renew_after_ms`` 取到 0 表示服务端本次响应根本没带这个字段，
# 而不是"要求 0ms TTL / 0ms 续期一次"。
SERVER_LEASE_MS_UNSET = 0

# 续期间隔必须严格小于 TTL 的 1/2：租约有效期内至少留一次重试机会，
# 单次网络抖动或一次 RPC 超时都不足以让租约过期。
# 定义在传输层的无依赖模块里，让"校验不变量"与"校验响应合同"共用同一个常数。
LEASE_RENEW_SAFETY_DIVISOR = 2


def decode_server_lease_ms(response_value_ms: int, current: int | None) -> int | None:
    """解码服务端下发的租约时序字段，未下发时沿用上一次的权威值。

    只识别"未下发"这一种情况；任何非 0 值都原样透出，由
    ``LeaseRenewalWindow`` 统一校验 ``renew_after_ms * 2 < ttl_ms``
    并在越界时显式抛错——传输层不做任何裁剪或过滤。

    "沿用上一次"沿用的是**服务端权威值**，不是本地默认值：服务端从未下发过
    时返回 ``None``，让上层显式失败而不是拿本地 ``LeasePolicy()`` 顶上。
    """
    if response_value_ms == SERVER_LEASE_MS_UNSET:
        return current
    return response_value_ms


# 续期节拍与 TTL 的下限：低于此值说明服务端根本没下发有效时序。
MIN_LEASE_RENEW_AFTER_MS = 1_000
MIN_LEASE_TTL_MS = MIN_LEASE_RENEW_AFTER_MS * LEASE_RENEW_SAFETY_DIVISOR


@dataclass(frozen=True)
class ServerLeaseFields:
    """Lease 响应里的租约字段快照。

    proto3 标量与 Direct 的 JSON 都没有 presence 语义，取到 0 / "" 一律表示
    服务端本次没带该字段。本结构只负责取值，校验交给调用方。
    """

    lease_id: str
    expires_at_ms: int
    renew_after_ms: int
    ttl_ms: int
    revoked: bool


def read_lease_fields(response: Any) -> ServerLeaseFields:
    """从 Lease 响应读出租约字段；只取值不校验、不抛错。"""
    return ServerLeaseFields(
        lease_id=getattr(response, "lease_id", "") or "",
        expires_at_ms=int(getattr(response, "expires_at_ms", 0) or 0),
        renew_after_ms=int(getattr(response, "renew_after_ms", 0) or 0),
        ttl_ms=int(getattr(response, "ttl_ms", 0) or 0),
        revoked=bool(getattr(response, "revoked", False)),
    )


class ServerLeaseCadence:
    """服务端租约时序的持有者，由 ``TransportBase`` 混入。

    Gateway / Direct 两个实现共用同一份语义：谁都不会把服务端下发的
    ``ttl_ms`` / ``renew_after_ms`` 丢在传输层里。
    """

    # 类级默认值即"服务端尚未下发过时序"，子类无需在 ``__init__`` 里重复初始化。
    _server_renew_after_ms: int | None = None
    _server_lease_ttl_ms: int | None = None

    @property
    def lease_renew_after_ms(self) -> int | None:
        """服务端最近一次 Lease 响应下发的续期周期（毫秒）。

        ``HeartbeatReporter`` 每个续期周期读取本值并交给 ``LeaseRenewalWindow``
        校验后采纳，因此服务端中途调整节拍能在下一拍生效，而不是被
        ``send_heartbeat`` 的 ``bool`` 返回值丢掉（原 B6 遗留缺口）。
        ``None`` 表示服务端尚未下发过节拍。
        """
        return self._server_renew_after_ms

    @property
    def lease_ttl_ms(self) -> int | None:
        """服务端最近一次 Lease 响应下发的权威 TTL（毫秒）。

        ``None`` 表示服务端从未下发过 ``ttl_ms``——此时 Worker 无法校验
        ``renew_after_ms * 2 < ttl_ms``，必须显式失败，绝不能拿本地
        ``LeasePolicy()`` 默认值假定，否则服务端收紧 TTL 时守卫会静默通过。
        """
        return self._server_lease_ttl_ms

    def adopt_server_lease_window(self, ttl_ms: int, renew_after_ms: int) -> None:
        """记下本次 Lease 响应的时序，供 ``HeartbeatReporter`` 下一拍读回。

        服务端本次未下发某字段时保持已知的权威值不变，绝不回落到"未知"。
        """
        self._server_lease_ttl_ms = decode_server_lease_ms(ttl_ms, self._server_lease_ttl_ms)
        self._server_renew_after_ms = decode_server_lease_ms(renew_after_ms, self._server_renew_after_ms)


__all__ = [
    "MIN_LEASE_RENEW_AFTER_MS",
    "MIN_LEASE_TTL_MS",
    "ServerLeaseFields",
    "read_lease_fields",
    "LEASE_RENEW_SAFETY_DIVISOR",
    "SERVER_LEASE_MS_UNSET",
    "ServerLeaseCadence",
    "decode_server_lease_ms",
]
