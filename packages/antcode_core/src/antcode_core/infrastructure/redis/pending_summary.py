"""Normalize XPENDING summaries across redis-py response shapes."""

from __future__ import annotations

from typing import Any


def empty_pending_summary() -> dict[str, Any]:
    return {"pending_count": 0, "min_id": None, "max_id": None, "consumers": {}}


def parse_pending_summary(result: Any) -> dict[str, Any]:
    if not result:
        return empty_pending_summary()
    if isinstance(result, dict):
        pending_value = result.get("pending", result.get("pending_count", 0))
        values = (
            pending_value,
            result.get("min", result.get("min_id")),
            result.get("max", result.get("max_id")),
            result.get("consumers", []),
        )
    else:
        values = (result[0], result[1], result[2], result[3])
    return {
        "pending_count": int(values[0] or 0),
        "min_id": _decode_optional_text(values[1]),
        "max_id": _decode_optional_text(values[2]),
        "consumers": _parse_pending_consumers(values[3]),
    }


def _parse_pending_consumers(items: Any) -> dict[str, int]:
    consumers: dict[str, int] = {}
    for item in items or []:
        if isinstance(item, dict):
            name, count = item.get("name"), item.get("pending", 0)
        else:
            name, count = item[0], item[1]
        consumers[_decode_text(name)] = int(count or 0)
    return consumers


def _decode_optional_text(value: Any) -> str | None:
    return None if value is None else _decode_text(value)


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


__all__ = ["empty_pending_summary", "parse_pending_summary"]
