"""Worker 上报的能力快照必须能通过控制面的线协议门禁。

这是"两端不会各自漂移"的结构性锁：门禁装在控制面，声明发生在 Worker，中间还隔着
``encode_capabilities`` / ``decode_capabilities`` 的 JSON 编解码。任何一端改了键名、
类型或编码方式，本文件就会红，而不是等到混跑现场才发现 Lease 全被拒。
"""

from __future__ import annotations

from antcode_contracts.capabilities import decode_capabilities, encode_capabilities
from antcode_contracts.wire_contract import (
    WIRE_CONTRACT_CAPABILITY,
    WORKER_WIRE_CONTRACT_VERSION,
    require_supported_wire_contract,
)
from antcode_worker.heartbeat.capability_detector import CapabilityDetector


def _snapshot_over_the_wire(capabilities: dict) -> dict:
    """走一遍 Lease 请求真实经历的编解码，别让测试比生产宽松。"""
    return decode_capabilities(encode_capabilities(capabilities))


def test_detected_snapshot_declares_the_current_contract() -> None:
    capabilities = CapabilityDetector().detect_all(force_refresh=True, task_types=["code"])

    assert capabilities[WIRE_CONTRACT_CAPABILITY] == WORKER_WIRE_CONTRACT_VERSION


def test_detected_snapshot_passes_the_control_plane_gate() -> None:
    capabilities = CapabilityDetector().detect_all(force_refresh=True, task_types=["code"])

    assert require_supported_wire_contract(_snapshot_over_the_wire(capabilities)) == WORKER_WIRE_CONTRACT_VERSION


def test_cached_snapshot_keeps_declaring_the_contract() -> None:
    """续租读的是缓存快照；契约一旦从缓存里掉出去，Lease 会在运行中途开始被拒。"""
    detector = CapabilityDetector()
    detector.detect_all(force_refresh=True, task_types=["code"])

    cached = detector.detect_all()

    assert require_supported_wire_contract(_snapshot_over_the_wire(cached)) == WORKER_WIRE_CONTRACT_VERSION


def test_task_type_refresh_keeps_declaring_the_contract() -> None:
    detector = CapabilityDetector()
    detector.detect_all(force_refresh=True, task_types=["code"])

    refreshed = detector.detect_all(task_types=["code", "spider"])

    assert refreshed[WIRE_CONTRACT_CAPABILITY] == WORKER_WIRE_CONTRACT_VERSION
