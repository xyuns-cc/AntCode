"""Gateway SpiderData producer/consumer shared input limits."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_BATCH_ITEMS = 500
DEFAULT_MAX_BATCH_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_ITEM_BYTES = 256 * 1024
DEFAULT_MAX_TEXT_LENGTH = 8192


@dataclass(frozen=True)
class SpiderIngestLimits:
    max_batch_items: int
    max_batch_bytes: int
    max_item_bytes: int
    max_text_length: int

    @property
    def max_safe_batch_items(self) -> int:
        byte_bound = max(1, self.max_batch_bytes // self.max_item_bytes - 1)
        return min(self.max_batch_items, byte_bound)

    @classmethod
    def from_env(cls) -> SpiderIngestLimits:
        return cls(
            max_batch_items=_positive_env("SPIDER_MAX_BATCH_ITEMS", DEFAULT_MAX_BATCH_ITEMS),
            max_batch_bytes=_positive_env("SPIDER_MAX_BATCH_BYTES", DEFAULT_MAX_BATCH_BYTES),
            max_item_bytes=_positive_env("SPIDER_MAX_ITEM_BYTES", DEFAULT_MAX_ITEM_BYTES),
            max_text_length=_positive_env("SPIDER_MAX_TEXT_LENGTH", DEFAULT_MAX_TEXT_LENGTH),
        )


def _positive_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def validate_spider_json(value: Any, max_bytes: int) -> None:
    """Validate one SpiderData JSON document before decoding it."""
    if not isinstance(value, (str, bytes)):
        raise ValueError("SpiderData data 必须是 JSON 字符串或 UTF-8 字节")
    try:
        encoded = value.encode("utf-8") if isinstance(value, str) else value
    except UnicodeEncodeError as exc:
        raise ValueError("SpiderData data 必须是合法 UTF-8") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"SpiderData data 编码大小超限: {len(encoded)} > {max_bytes}")
    try:
        text = encoded.decode("utf-8")
        json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError("SpiderData data 必须是严格合法 JSON") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 非有限数值不合法: {value}")


__all__ = ["SpiderIngestLimits", "validate_spider_json"]
