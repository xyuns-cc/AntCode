"""Strict JSON serialization for authoritative Lease state."""

from __future__ import annotations

import json
from typing import Any


def serialize_lease_value(value: dict[str, Any] | None, *, preserve_empty: bool = False) -> str:
    if value is None or (not value and not preserve_empty):
        return ""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


__all__ = ["serialize_lease_value"]
