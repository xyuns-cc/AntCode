"""Normalize capability values supplied by Worker heartbeats."""

from __future__ import annotations

import contextlib

from antcode_core.common.serialization import from_json


def normalize_capabilities(capabilities: dict) -> dict:
    normalized: dict = {}
    for key, value in capabilities.items():
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith(("{", "[")):
                with contextlib.suppress(Exception):
                    value = from_json(raw)
            elif raw.lower() in {"true", "false"}:
                value = raw.lower() == "true"
        normalized[key] = value
    return normalized


__all__ = ["normalize_capabilities"]
