"""Validated Crawl batch overrides for Rule dispatch payloads."""

from __future__ import annotations

from typing import Any


def batch_rule_overrides(batch: Any) -> dict[str, int]:
    mappings = (
        ("max_pages", "max_pages"),
        ("max_retries", "retry_count"),
        ("timeout", "timeout"),
        ("max_concurrency", "concurrent_requests"),
        ("max_depth", "max_depth"),
    )
    overrides = {
        target: _batch_int_value(batch, source) for source, target in mappings if getattr(batch, source) is not None
    }
    if batch.request_delay is not None:
        overrides["request_delay"] = _request_delay_milliseconds(batch)
    return overrides


def _request_delay_milliseconds(batch: Any) -> int:
    try:
        return int(float(batch.request_delay) * 1000)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"batch 配置无效: batch_id={batch.public_id} field=request_delay value={batch.request_delay!r}"
        ) from exc


def _batch_int_value(batch: Any, field: str) -> int:
    value = getattr(batch, field)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"batch 配置无效: batch_id={batch.public_id} field={field} value={value!r}") from exc


__all__ = ["batch_rule_overrides"]
