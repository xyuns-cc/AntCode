"""Redis cursor reads for one execution's scraped items."""

from __future__ import annotations

import json
from typing import Any


async def read_spider_stream(run_id: str, start_id: str, count: int) -> list[Any]:
    from antcode_core.common.config import settings
    from antcode_core.infrastructure.redis import get_redis_client
    from antcode_core.infrastructure.redis.keys import RedisKeys

    redis = await get_redis_client()
    if redis is None:
        raise RuntimeError("Redis client unavailable")
    stream_key = RedisKeys(namespace=settings.REDIS_NAMESPACE).spider_data_stream(run_id)
    min_id = f"({start_id}" if start_id and start_id != "0" else "-"
    return list(await redis.xrange(stream_key, min=min_id, max="+", count=count) or [])


def decode_spider_items(raw: list[Any], start_id: str) -> tuple[list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    last_id = start_id
    for msg_id, fields in raw:
        decoded_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        last_id = decoded_id
        items.append({"_id": decoded_id, **_decode_fields(fields)})
    return items, last_id


def _decode_fields(fields: dict[Any, Any] | None) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for raw_key, raw_value in (fields or {}).items():
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        value = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        decoded[key] = _decode_value(key, value)
    return decoded


def _decode_value(key: str, value: Any) -> Any:
    if key != "data" or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


__all__ = ["decode_spider_items", "read_spider_stream"]
