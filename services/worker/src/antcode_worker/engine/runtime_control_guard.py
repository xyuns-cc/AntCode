"""P1-DR-04: 运行时控制事件的 deadline 判定（权威时钟由 transport 提供）。"""

from __future__ import annotations

from typing import Any


def _require_live_runtime_control(payload: dict[str, Any], now_ms: int) -> None:
    """``now_ms`` 必须来自 transport 权威时钟（Direct=Redis TIME），与
    Master 生成 deadline 的时钟一致，消除跨机器 wall clock 偏移。"""
    try:
        expires_at_ms = int(payload.get("expires_at_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("运行时控制事件 expires_at_ms 无效") from exc
    if expires_at_ms <= 0:
        raise RuntimeError("运行时控制事件缺少 expires_at_ms")
    if now_ms >= expires_at_ms:
        raise RuntimeError("运行时控制事件已过期，拒绝执行")


__all__ = ["_require_live_runtime_control"]
