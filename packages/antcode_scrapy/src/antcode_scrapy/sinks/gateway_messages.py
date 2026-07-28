"""Protobuf builders for the Gateway SpiderData sink."""

from __future__ import annotations

from typing import Any


def build_item(
    *,
    spider_name: str,
    item_id: str,
    item_type: str,
    data_json: str,
    url: str,
    timestamp: str,
    sequence: int,
):
    from antcode_contracts import data_pb2

    return data_pb2.SpiderDataItem(
        item_id=item_id,
        spider_name=spider_name,
        item_type=item_type or "default",
        data=data_json.encode("utf-8"),
        url=url,
        timestamp=timestamp,
        sequence=sequence,
    )


def build_batch(
    *,
    worker_id: str,
    run_id: str,
    project_id: str,
    lease_id: str,
    items: list[Any],
    meta_fields: dict[str, str],
):
    from antcode_contracts import data_pb2

    batch = data_pb2.SpiderDataBatch(
        worker_id=worker_id,
        run_id=run_id,
        project_id=project_id,
        lease_id=lease_id,
    )
    batch.items.extend(items)
    if meta_fields:
        batch.meta.CopyFrom(build_meta(meta_fields))
    return batch


_META_STRING_FIELDS = (
    "status",
    "last_item_at",
    "spider_name",
    "started_at",
    "finished_at",
    "config",
    "errors",
)
_META_INT_FIELDS = ("items_count", "pages_count", "errors_count")


def build_meta(fields: dict[str, str]):
    from antcode_contracts import data_pb2

    # 与 worker 侧 GatewayTransport._build_spider_meta_update 对齐:字符串字段仅在
    # 非空时写入,可选数值字段(pages_count/errors_count/duration_ms)仅在提供时写入,
    # 这样 gateway 端基于 HasField 的提取才能拿到完整 meta;否则新增的 8 个 proto
    # 字段会被静默丢弃,导致 Scrapy gateway-mode 落库的 meta 不完整。
    values: dict[str, Any] = {}
    for name in _META_STRING_FIELDS:
        value = fields.get(name)
        if value:
            values[name] = str(value)
    for name in _META_INT_FIELDS:
        value = fields.get(name)
        if value is not None and value != "":
            values[name] = int(value)
    duration_ms = fields.get("duration_ms")
    if duration_ms is not None and duration_ms != "":
        values["duration_ms"] = float(duration_ms)
    return data_pb2.SpiderMetaUpdate(**values)


__all__ = ["build_batch", "build_item", "build_meta"]
