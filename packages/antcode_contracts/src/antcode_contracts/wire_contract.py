"""Worker 线协议契约版本 —— Lease 签发的 fail-closed 前置条件。

本次发布有多处 wire 格式断裂且**没有任何一处带版本协商**：ready stream key 加了
Cluster hash-tag、ready 帧新增必需字段并把 ``params`` / ``environment`` 改成密文
信封、Worker HTTP HMAC 由 v1 升到 v2。后果不是"连不上"，而是**连得上却在撒谎**：
版本不一致的 Worker 照样通过认证、照样拿到 Lease，控制台显示在线健康，而
Gateway 下发的任务里 ``params`` / ``environment`` 是空的（旧 Worker 读不到新增的
``sealed_ready_payload``，proto3 未知字段被静默丢弃），任务被照常执行。全链路
没有一处会报错。

所以 Worker 必须在**每一次 Lease 请求**里声明自己的线协议契约版本，控制面在签发
前**精确比对**，不等即拒发。选在 Lease 上是因为它是唯一的存活信号：拒发即没有
心跳（控制台判离线）、没有 Lease 能力快照（派发的前置条件不成立），版本错配再也
无法伪装成健康。这条通道对 Gateway（gRPC ``ControlService.Lease``）与 Direct
（HTTP ``direct-control/lease``）两种传输同时生效。

不设"支持到第 N 版"的容忍区间：上线形态是五镜像同批 + 停机窗口，控制面与 Worker
永远同版本，区间没有对应的部署形态。任何 wire 断裂落地时把
``WORKER_WIRE_CONTRACT_VERSION`` +1 即可。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Worker 能力快照里承载契约版本的键名。走 ``capabilities`` 而不是新 proto 字段，
#: 是因为 Gateway 的 ``LeaseRequest.capabilities`` 与 Direct 的 JSON lease 请求本就
#: 逐字段透传这份快照，无需改动 wire 定义即可覆盖两种传输。
WIRE_CONTRACT_CAPABILITY = "wire_contract"

#: 本次发布的契约版本：hash-tag ready key + 密文 ready 帧 + HMAC v2 + Lease ttl_ms
#: (v2)，外加运行时控制失败回包的 ``data`` 必须携带结构化 ``error_code`` (v3)，
#: 再加心跳 ``Metrics`` 新增生效单任务限额 20/21 号字段 (v4)。
WORKER_WIRE_CONTRACT_VERSION = 4


class WorkerWireContractError(RuntimeError):
    """Worker 声明的线协议契约与控制面不一致，Lease 必须拒发。"""


def require_supported_wire_contract(capabilities: Mapping[str, Any] | None) -> int:
    """校验并返回 Worker 契约版本；与控制面不一致抛 ``WorkerWireContractError``。"""
    declared = (capabilities or {}).get(WIRE_CONTRACT_CAPABILITY)
    if declared is None:
        raise WorkerWireContractError(
            f"Worker 未声明 {WIRE_CONTRACT_CAPABILITY}（控制面为 v{WORKER_WIRE_CONTRACT_VERSION}）。"
            "该 Worker 与控制面不在同一发布窗口，继续运行会带着空 params/environment "
            "执行任务；请把它升级到与控制面相同的版本"
        )
    # ``bool`` 是 ``int`` 的子类但语义不同，显式排除以免 ``wire_contract: true`` 通过。
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise WorkerWireContractError(f"Worker 声明的 {WIRE_CONTRACT_CAPABILITY} 不是整数版本号: {declared!r}")
    if declared != WORKER_WIRE_CONTRACT_VERSION:
        raise WorkerWireContractError(
            f"Worker 线协议契约版本不匹配: v{declared}（控制面为 v{WORKER_WIRE_CONTRACT_VERSION}）。"
            "控制面与 Worker 必须同批升级，否则任务会带着空 params/environment 执行"
        )
    return declared


def wire_contract_capability() -> dict[str, int]:
    """Worker 声明自身契约版本时并入能力快照的键值对。"""
    return {WIRE_CONTRACT_CAPABILITY: WORKER_WIRE_CONTRACT_VERSION}


__all__ = [
    "WIRE_CONTRACT_CAPABILITY",
    "WORKER_WIRE_CONTRACT_VERSION",
    "WorkerWireContractError",
    "require_supported_wire_contract",
    "wire_contract_capability",
]
