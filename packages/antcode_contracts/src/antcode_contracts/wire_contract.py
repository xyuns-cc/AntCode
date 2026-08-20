"""Worker 线协议契约版本 —— Lease 签发的 fail-closed 前置条件。

本次发布有多处 wire 格式断裂且**没有任何一处带版本协商**：ready stream key 加了
Cluster hash-tag、ready 帧新增必需字段并把 ``params`` / ``environment`` 改成密文
信封、Worker HTTP HMAC 由 v1 升到 v2。后果不是"连不上"，而是**连得上却在撒谎**：
未同窗口升级的旧 Worker 照样通过认证、照样拿到 Lease，控制台显示在线健康，而
Gateway 下发的任务里 ``params`` / ``environment`` 是空的（旧 Worker 读不到新增的
``sealed_ready_payload``，proto3 未知字段被静默丢弃），任务被照常执行。全链路
没有一处会报错。

所以 Worker 必须在**每一次 Lease 请求**里声明自己的线协议契约版本，控制面在签发
前比对，不兼容即拒发。选在 Lease 上是因为它是唯一的存活信号：拒发即没有心跳
（控制台判离线）、没有 Lease 能力快照（派发的前置条件不成立），版本错配再也无法
伪装成健康。这条通道对 Gateway（gRPC ``ControlService.Lease``）与 Direct
（HTTP ``direct-control/lease``）两种传输同时生效。

比对是**双向**的：低于 ``MIN_SUPPORTED_WORKER_WIRE_CONTRACT`` 说明 Worker 落后，
高于 ``WORKER_WIRE_CONTRACT_VERSION`` 说明控制面落后。后者让本版本的控制面能挡住
未来版本的 Worker，下一次 wire 断裂不必再重复造轮子。

**任何**无协商的 wire 断裂落地时，``WORKER_WIRE_CONTRACT_VERSION`` 必须 +1，并同步
抬高 ``MIN_SUPPORTED_WORKER_WIRE_CONTRACT``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Worker 能力快照里承载契约版本的键名。走 ``capabilities`` 而不是新 proto 字段，
#: 是因为 Gateway 的 ``LeaseRequest.capabilities`` 与 Direct 的 JSON lease 请求本就
#: 逐字段透传这份快照，无需改动 wire 定义即可覆盖两种传输。
WIRE_CONTRACT_CAPABILITY = "wire_contract"

#: 未声明契约版本的 Worker 一律按此版本处理——``origin/main`` 及更早的 Worker 都不
#: 认识这个键，"缺失"本身就是可判定的版本信息，不是需要容错的缺陷。
LEGACY_WIRE_CONTRACT_VERSION = 1

#: 本次发布的契约版本：hash-tag ready key + 密文 ready 帧 + HMAC v2 + Lease ttl_ms
#: (v2)，外加运行时控制失败回包的 ``data`` 必须携带结构化 ``error_code`` (v3)，
#: 再加心跳 ``Metrics`` 新增生效单任务限额 20/21 号字段 (v4)。
#:
#: v4 必须单独占一个版本号，不能并进尚未发布的 v3：断裂方向是**新 Worker → 旧控制面**。
#: 新 Worker 的 Direct 心跳会带上这两个键，而旧控制面的 ``DirectLeaseMetrics`` 是
#: ``extra="forbid"`` —— 每一次续租被判 422 → 租约永不续期 → 失租回收 → 容器无限
#: 重启。并进 v3 就要赌"v3 从未部署到任何地方"，而测试机此刻就跑着 cbe3dd2 构建的
#: v3 镜像，这个赌注当场就是输的。抬版本号把它变成 Lease 拒发这种一眼可见的失败。
WORKER_WIRE_CONTRACT_VERSION = 4

#: 控制面接受的最低 Worker 契约版本。
MIN_SUPPORTED_WORKER_WIRE_CONTRACT = 4


class WorkerWireContractError(RuntimeError):
    """Worker 声明的线协议契约与控制面不兼容，Lease 必须拒发。"""


def declared_wire_contract(capabilities: Mapping[str, Any] | None) -> int:
    """读出 Worker 声明的契约版本；缺失即 ``LEGACY_WIRE_CONTRACT_VERSION``。

    值必须是整数：``bool`` 是 ``int`` 的子类但语义不同，显式排除以免
    ``wire_contract: true`` 被当成 v1 通过。
    """
    declared = (capabilities or {}).get(WIRE_CONTRACT_CAPABILITY)
    if declared is None:
        return LEGACY_WIRE_CONTRACT_VERSION
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise WorkerWireContractError(f"Worker 声明的 {WIRE_CONTRACT_CAPABILITY} 不是整数版本号: {declared!r}")
    return declared


def require_supported_wire_contract(capabilities: Mapping[str, Any] | None) -> int:
    """校验并返回 Worker 契约版本；不兼容抛 ``WorkerWireContractError``。"""
    declared = declared_wire_contract(capabilities)
    if declared < MIN_SUPPORTED_WORKER_WIRE_CONTRACT:
        raise WorkerWireContractError(
            f"Worker 线协议契约版本过旧: v{declared}"
            f"（控制面要求 >= v{MIN_SUPPORTED_WORKER_WIRE_CONTRACT}）。"
            "该 Worker 与控制面不在同一发布窗口，继续运行会带着空 params/environment "
            "执行任务；请把它升级到与控制面相同的版本"
        )
    if declared > WORKER_WIRE_CONTRACT_VERSION:
        raise WorkerWireContractError(
            f"Worker 线协议契约版本高于控制面: v{declared}（控制面为 v{WORKER_WIRE_CONTRACT_VERSION}）。请先升级控制面"
        )
    return declared


def wire_contract_capability() -> dict[str, int]:
    """Worker 声明自身契约版本时并入能力快照的键值对。"""
    return {WIRE_CONTRACT_CAPABILITY: WORKER_WIRE_CONTRACT_VERSION}


__all__ = [
    "LEGACY_WIRE_CONTRACT_VERSION",
    "MIN_SUPPORTED_WORKER_WIRE_CONTRACT",
    "WIRE_CONTRACT_CAPABILITY",
    "WORKER_WIRE_CONTRACT_VERSION",
    "WorkerWireContractError",
    "declared_wire_contract",
    "require_supported_wire_contract",
    "wire_contract_capability",
]
