"""Gateway runtime-control 权威时钟与内部消息转换。"""

from __future__ import annotations

import time
from typing import Any

from antcode_worker.transport.base import ControlMessage

NANOSECONDS_PER_MILLISECOND = 1_000_000


class GatewayRuntimeControlClock:
    """将 Gateway 的 Redis TIME 观测值映射到本机单调时钟。"""

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None

    def to_message(self, event: Any, decoder: Any) -> ControlMessage:
        runtime = decoder.decode_runtime_control(event.runtime_control)
        expires_at_ms = int(runtime.get("expires_at_ms", 0) or 0)
        observed_at_ms = int(getattr(event.runtime_control, "gateway_observed_at_ms", 0) or 0)
        self._require_live_delivery(expires_at_ms, observed_at_ms)
        self._anchor = (observed_at_ms, time.monotonic_ns())
        args = runtime.get("args", {})
        return ControlMessage(
            control_type="runtime_manage",
            payload={
                "request_id": runtime.get("request_id", ""),
                "action": runtime.get("action", ""),
                "expires_at_ms": expires_at_ms,
                "params": runtime.get("params", {}),
                "args": args,
                "payload": args,
            },
            receipt=getattr(event, "event_id", "") or "",
        )

    def now_ms(self) -> int:
        if self._anchor is None:
            raise RuntimeError("Gateway runtime control 缺少 Redis 权威时钟观测值")
        observed_at_ms, received_at_ns = self._anchor
        elapsed_ns = time.monotonic_ns() - received_at_ns
        return observed_at_ms + elapsed_ns // NANOSECONDS_PER_MILLISECOND

    @staticmethod
    def _require_live_delivery(expires_at_ms: int, observed_at_ms: int) -> None:
        if observed_at_ms <= 0:
            raise RuntimeError("Gateway runtime control 缺少 Redis 权威时钟观测值")
        if expires_at_ms <= observed_at_ms:
            raise RuntimeError("Gateway 投递了已过期的 runtime control")


__all__ = ["GatewayRuntimeControlClock"]
