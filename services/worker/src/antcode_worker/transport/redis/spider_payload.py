"""Canonical Direct SpiderData HTTP payload validation."""

from __future__ import annotations

import json
from typing import Any

from antcode_core.spider_ingest import SpiderIngestLimits, validate_spider_json

_TEXT_FIELDS = ("item_id", "run_id", "project_id", "spider_name", "item_type", "url", "timestamp")
_ITEM_FIELDS = (*_TEXT_FIELDS, "data", "sequence")


def normalize_spider_items(run_id: str, items: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    limits = SpiderIngestLimits.from_env()
    if len(items) > limits.max_batch_items:
        raise ValueError(f"SpiderData batch items 超限: {len(items)} > {limits.max_batch_items}")
    payloads = [_normalize_item(run_id, item, limits) for item in items]
    project_ids = {payload["project_id"] for payload in payloads}
    if len(project_ids) != 1:
        raise ValueError("SpiderData batch 必须属于同一个非空 project_id")
    _require_size(payloads, limits.max_batch_bytes, "batch")
    return project_ids.pop(), payloads


def _normalize_item(run_id: str, item: Any, limits: SpiderIngestLimits) -> dict[str, Any]:
    raw = item.to_redis_dict() if hasattr(item, "to_redis_dict") else dict(item)
    if set(raw) != set(_ITEM_FIELDS):
        raise ValueError("SpiderData item 字段集合不合法")
    if raw["run_id"] != run_id:
        raise ValueError("SpiderData item run_id 与 batch 不一致")
    if not raw["project_id"]:
        raise ValueError("SpiderData item project_id 不能为空")
    for name in _TEXT_FIELDS:
        value = raw[name]
        if not isinstance(value, str) or len(value) > limits.max_text_length:
            raise ValueError(f"SpiderData {name} 不合法")
    item_id = raw["item_id"].strip()
    if not item_id:
        raise ValueError("SpiderData item_id 不能为空")
    validate_spider_json(raw["data"], limits.max_item_bytes)
    try:
        sequence = int(raw["sequence"])
    except (TypeError, ValueError) as exc:
        raise ValueError("SpiderData sequence 必须为正整数") from exc
    if isinstance(raw["sequence"], bool) or sequence <= 0 or str(sequence) != str(raw["sequence"]):
        raise ValueError("SpiderData sequence 必须为正整数")
    payload = dict(raw)
    payload["item_id"] = item_id
    payload["sequence"] = str(sequence)
    _require_size(payload, limits.max_item_bytes, "item")
    return payload


def _require_size(value: Any, limit: int, label: str) -> None:
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    if size > limit:
        raise ValueError(f"SpiderData {label} 编码大小超限: {size} > {limit}")


__all__ = ["normalize_spider_items"]
