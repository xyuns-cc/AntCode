"""Validated Gateway SpiderData sink configuration."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from antcode_core.spider_ingest import SpiderIngestLimits

DEFAULT_BATCH_SIZE = 30
DEFAULT_FLUSH_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class GatewaySinkConfig:
    batch_size: int
    flush_interval_seconds: float
    limits: SpiderIngestLimits

    @classmethod
    def from_env(cls) -> GatewaySinkConfig:
        limits = SpiderIngestLimits.from_env()
        batch_size = _positive_int_env("ANTCODE_SPIDER_GATEWAY_BATCH_SIZE", DEFAULT_BATCH_SIZE)
        maximum = limits.max_safe_batch_items
        if batch_size > maximum:
            raise ValueError(f"ANTCODE_SPIDER_GATEWAY_BATCH_SIZE 不能超过 {maximum}")
        return cls(
            batch_size=batch_size,
            flush_interval_seconds=_positive_float_env(
                "ANTCODE_SPIDER_GATEWAY_FLUSH_INTERVAL",
                DEFAULT_FLUSH_INTERVAL_SECONDS,
            ),
            limits=limits,
        )


def _positive_int_env(name: str, default: int) -> int:
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


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是有限正数") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return value


__all__ = ["GatewaySinkConfig"]
