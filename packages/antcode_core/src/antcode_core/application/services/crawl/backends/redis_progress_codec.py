"""Redis Hash encoding helpers for Crawl progress data."""

from __future__ import annotations

from typing import Any

from antcode_core.common.serialization import from_json, to_json


def mapping_args(data: dict[str, Any]) -> list[str]:
    return [item for pair in data.items() for item in (pair[0], to_json(pair[1]))]


def decode_hash(data: dict) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in data.items():
        key_text = key.decode("utf-8") if isinstance(key, bytes) else key
        value_text = value.decode("utf-8") if isinstance(value, bytes) else value
        decoded[key_text] = from_json(value_text)
    return decoded


__all__ = ["decode_hash", "mapping_args"]
