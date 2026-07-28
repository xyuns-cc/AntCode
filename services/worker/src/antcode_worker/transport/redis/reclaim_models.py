"""Pending task reclaim configuration and Redis response models."""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DEFAULT_MAX_RECLAIM_COUNT = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_RECLAIM_INTERVAL_SECONDS = 30.0
DEFAULT_DEAD_LETTER_TTL_SECONDS = 7 * 86_400


@dataclass(frozen=True)
class ReclaimConfig:
    """Validated task PEL recovery configuration."""

    min_idle_time_ms: int = 0
    max_reclaim_count: int = DEFAULT_MAX_RECLAIM_COUNT
    check_interval_seconds: float = DEFAULT_RECLAIM_INTERVAL_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    enable_dead_letter: bool = True
    dead_letter_ttl_seconds: int = DEFAULT_DEAD_LETTER_TTL_SECONDS

    def __post_init__(self) -> None:
        _require_non_negative_int("min_idle_time_ms", self.min_idle_time_ms)
        _require_positive_int("max_reclaim_count", self.max_reclaim_count)
        _require_non_negative_int("max_retries", self.max_retries)
        _require_positive_number("check_interval_seconds", self.check_interval_seconds)
        _require_positive_int("dead_letter_ttl_seconds", self.dead_letter_ttl_seconds)


@dataclass
class ReclaimedTask:
    message_id: str
    data: dict[str, str]
    idle_time_ms: int
    delivery_count: int
    last_delivery_time: datetime | None = None


@dataclass
class ReclaimStats:
    total_reclaimed: int = 0
    total_dead_lettered: int = 0
    last_reclaim_time: datetime | None = None
    reclaim_errors: int = 0
    stream_stats: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingEntry:
    message_id: str
    consumer: str
    idle_time_ms: int
    delivery_count: int


def parse_pending_entry(raw: Any) -> PendingEntry:
    if isinstance(raw, dict):
        message_id = raw.get("message_id")
        consumer = raw.get("consumer")
        idle_time = raw.get("time_since_delivered")
        delivery_count = raw.get("times_delivered")
    elif isinstance(raw, (list, tuple)) and len(raw) >= 4:
        message_id, consumer, idle_time, delivery_count = raw[:4]
    else:
        raise RuntimeError("XPENDING 返回了无效 entry")
    if message_id is None or consumer is None or idle_time is None or delivery_count is None:
        raise RuntimeError("XPENDING entry 缺少必需字段")
    entry = PendingEntry(
        message_id=text(message_id),
        consumer=text(consumer),
        idle_time_ms=int(idle_time),
        delivery_count=int(delivery_count),
    )
    _validate_pending_entry(entry)
    return entry


def decode_pending_summary(result: dict[str, Any]) -> dict[str, Any]:
    consumers = result.get("consumers") or []
    counts = {
        text(item.get("name", "")): int(item.get("pending", 0) or 0) for item in consumers if isinstance(item, dict)
    }
    return {
        "pending_count": int(result.get("pending", 0) or 0),
        "min_id": result.get("min"),
        "max_id": result.get("max"),
        "consumers": counts,
    }


def text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _validate_pending_entry(entry: PendingEntry) -> None:
    if not entry.message_id or not entry.consumer:
        raise RuntimeError("XPENDING entry 标识为空")
    if entry.idle_time_ms < 0 or entry.delivery_count < 1:
        raise RuntimeError("XPENDING entry 计数无效")


def _require_non_negative_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数")


def _require_positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} 必须是正整数")


def _require_positive_number(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是正数")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是有限正数")
