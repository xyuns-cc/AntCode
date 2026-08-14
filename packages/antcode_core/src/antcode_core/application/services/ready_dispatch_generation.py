"""Consumption-time fence for ready-stream dispatch generations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def require_dispatch_generation(payload: Mapping[str, Any], current_lease_id: str) -> None:
    dispatch_lease_id = str(payload.get("dispatch_lease_id") or "")
    if not dispatch_lease_id:
        raise ValueError("ready 消息缺少 dispatch_lease_id")
    if dispatch_lease_id != current_lease_id:
        raise ValueError("ready 消息 dispatch_lease_id 与当前 Lease 不匹配")
    dispatch_lease_gen = payload.get("dispatch_lease_gen")
    if isinstance(dispatch_lease_gen, bool) or not isinstance(dispatch_lease_gen, (int, str)):
        raise ValueError("ready 消息 dispatch_lease_gen 不合法")
    try:
        normalized = int(dispatch_lease_gen)
    except ValueError as exc:
        raise ValueError("ready 消息 dispatch_lease_gen 不合法") from exc
    if normalized < 1 or str(normalized) != str(dispatch_lease_gen):
        raise ValueError("ready 消息缺少有效 dispatch_lease_gen")


__all__ = ["require_dispatch_generation"]
