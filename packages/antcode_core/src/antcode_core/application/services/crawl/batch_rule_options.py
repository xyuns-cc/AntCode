"""Validated Crawl batch overrides for Rule dispatch payloads."""

from __future__ import annotations

from typing import Any

# 逐字复制到每个 seed 的字段：语义本来就是"每次请求"而不是批次总量。
_VERBATIM_FIELDS = (
    ("max_retries", "retry_count"),
    ("timeout", "timeout"),
    ("max_depth", "max_depth"),
)
# 批次总量字段：每个 seed 是一条独立 rule 任务、一个独立 Scrapy 进程，
# 逐字复制等于把上限乘以 seed 数（100 seed × 50 并发 = 5000 并发）。
_BATCH_TOTAL_FIELDS = (
    ("max_pages", "max_pages"),
    ("max_concurrency", "concurrent_requests"),
)


def batch_rule_overrides(batch: Any, *, seed_count: int) -> dict[str, int]:
    overrides = {
        target: _batch_int_value(batch, source)
        for source, target in _VERBATIM_FIELDS
        if getattr(batch, source) is not None
    }
    overrides.update(
        {
            target: _seed_share(_batch_int_value(batch, source), seed_count)
            for source, target in _BATCH_TOTAL_FIELDS
            if getattr(batch, source) is not None
        }
    )
    if batch.request_delay is not None:
        overrides["request_delay"] = _request_delay_milliseconds(batch)
    return overrides


def batch_seed_slots(batch: Any, *, seed_count: int) -> int:
    """同时允许在跑的 seed 进程数上限。

    并发是速率，平分后地板取 1：seed 数超过批次额度时每个 seed 仍占 1 并发，
    只有同时压住在跑的 seed 数，"在跑 seed 数 × 每 seed 并发"才不会超额度。
    """
    total = _batch_int_value(batch, "max_concurrency")
    return max(total // _seed_share(total, seed_count), 1)


def _seed_share(total: int, seed_count: int) -> int:
    return max(total // max(seed_count, 1), 1)


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


__all__ = ["batch_rule_overrides", "batch_seed_slots"]
