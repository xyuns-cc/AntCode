"""Trusted SpiderData ingestion for Direct Workers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from antcode_core.application.services.workers.run_ownership_service import require_worker_owns_spider_run
from antcode_core.application.services.workers.spider_run_access import StaleSpiderLeaseError
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_core.spider_ingest import SpiderIngestLimits, validate_spider_json
from antcode_core.spider_item_writer import IdempotentSpiderItemWriter
from antcode_core.spider_retention import SpiderRetention
from antcode_core.spider_write_fence import (
    SpiderWriteIdentity,
    write_fenced_spider_meta,
)
from fastapi import HTTPException, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1.workers_direct_models import DirectSpiderItemsRequest, DirectSpiderMetaRequest

_ITEM_FIELDS = (
    "item_id",
    "run_id",
    "project_id",
    "spider_name",
    "item_type",
    "data",
    "url",
    "timestamp",
    "sequence",
)


async def ingest_direct_spider_items(worker: Any, request: DirectSpiderItemsRequest) -> BaseResponse:
    redis, identity, keys = await _prepare_write(worker, request)
    payloads = [_normalize_item(item, request) for item in request.items]
    _require_batch_limits(payloads)
    retention = _retention()
    writer = IdempotentSpiderItemWriter(
        redis,
        stream_max_len=retention.stream_max_len,
        ttl_seconds=retention.ttl_seconds,
    )
    try:
        result = await writer.write(
            keys.spider_data_stream(request.run_id),
            keys.spider_item_ids_key(request.run_id),
            keys.spider_item_order_key(request.run_id),
            identity=identity,
            tombstone_key=keys.spider_tombstone_key(request.run_id),
            index_key=keys.spider_index_key(request.project_id),
            index_expiry_key=keys.spider_index_expiry_key(request.project_id),
            payloads=payloads,
        )
    except Exception as exc:
        _raise_write_error(exc, request.run_id)
    return success(
        {
            "written": True,
            "accepted": result.accepted,
            "inserted": result.inserted,
            "duplicates": result.duplicates,
        }
    )


async def ingest_direct_spider_meta(worker: Any, request: DirectSpiderMetaRequest) -> BaseResponse:
    redis, identity, keys = await _prepare_write(worker, request)
    fields = _normalize_meta(request)
    try:
        await write_fenced_spider_meta(
            redis,
            keys.spider_meta_key(request.run_id),
            identity=identity,
            tombstone_key=keys.spider_tombstone_key(request.run_id),
            marker_key=keys.spider_item_ids_key(request.run_id),
            index_key=keys.spider_index_key(request.project_id),
            index_expiry_key=keys.spider_index_expiry_key(request.project_id),
            fields=fields,
            ttl_seconds=_retention().ttl_seconds,
        )
    except Exception as exc:
        _raise_write_error(exc, request.run_id)
    return success({"written": True})


async def _prepare_write(worker: Any, request) -> tuple[Any, SpiderWriteIdentity, RedisKeys]:
    try:
        await require_worker_owns_spider_run(
            worker,
            request.run_id,
            request.project_id,
            lease_id=request.lease_id,
        )
    except StaleSpiderLeaseError as exc:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(exc)) from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    from antcode_web_api.routes.v1.workers_direct_control import _redis_client

    redis = await _redis_client()
    namespace = redis_namespace()
    identity = SpiderWriteIdentity(
        namespace=namespace,
        worker_id=str(worker.public_id),
        lease_id=request.lease_id,
        run_id=request.run_id,
        project_id=request.project_id,
    )
    return redis, identity, RedisKeys(namespace=namespace)


def _normalize_item(item: Mapping[str, Any], request: DirectSpiderItemsRequest) -> dict[str, Any]:
    if set(item) != set(_ITEM_FIELDS):
        raise HTTPException(status_code=422, detail="SpiderData item 字段集合不合法")
    if item["run_id"] != request.run_id or item["project_id"] != request.project_id:
        raise HTTPException(status_code=403, detail="SpiderData item 归属与请求不一致")
    sequence = item["sequence"]
    try:
        normalized_sequence = int(sequence)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="SpiderData sequence 必须为正整数") from exc
    if isinstance(sequence, bool) or normalized_sequence <= 0 or str(normalized_sequence) != str(sequence):
        raise HTTPException(status_code=422, detail="SpiderData sequence 必须为正整数")
    limits = SpiderIngestLimits.from_env()
    for name in ("item_id", "spider_name", "item_type", "url", "timestamp"):
        if not isinstance(item[name], str) or len(item[name]) > limits.max_text_length:
            raise HTTPException(status_code=422, detail=f"SpiderData {name} 不合法")
    item_id = item["item_id"].strip()
    if not item_id:
        raise HTTPException(status_code=422, detail="SpiderData item_id 不能为空")
    try:
        validate_spider_json(item["data"], limits.max_item_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized = dict(item)
    normalized["item_id"] = item_id
    normalized["sequence"] = str(normalized_sequence)
    return normalized


def _normalize_meta(request: DirectSpiderMetaRequest) -> dict[str, Any]:
    fields = {name: _normalize_meta_value(name, value) for name, value in request.meta.items()}
    for name, expected in (("run_id", request.run_id), ("project_id", request.project_id)):
        supplied = fields.get(name)
        if supplied is not None and str(supplied) != expected:
            raise HTTPException(status_code=403, detail=f"SpiderData meta {name} 归属不一致")
        fields[name] = expected
    _require_encoded_size(fields, SpiderIngestLimits.from_env().max_item_bytes, "meta")
    return fields


def _normalize_meta_value(name: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise HTTPException(status_code=422, detail=f"SpiderData meta {name} 必须是字符串或有限数值")
    if isinstance(value, float) and not math.isfinite(value):
        raise HTTPException(status_code=422, detail=f"SpiderData meta {name} 必须是字符串或有限数值")
    return str(value)


def _require_batch_limits(payloads: list[dict[str, Any]]) -> None:
    limits = SpiderIngestLimits.from_env()
    if len(payloads) > limits.max_batch_items:
        raise HTTPException(status_code=413, detail="SpiderData batch items 超限")
    for payload in payloads:
        _require_encoded_size(payload, limits.max_item_bytes, "item")
    _require_encoded_size(payloads, limits.max_batch_bytes, "batch")


def _require_encoded_size(value: Any, limit: int, label: str) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > limit:
        raise HTTPException(status_code=413, detail=f"SpiderData {label} 编码大小超限")


def _retention() -> SpiderRetention:
    return SpiderRetention.from_env(
        stream_max_len_env="SPIDER_STREAM_MAXLEN",
        ttl_seconds_env="SPIDER_META_TTL_SECONDS",
    )


def _raise_write_error(exc: Exception, run_id: str) -> None:
    message = str(exc)
    if "SPIDER_LEASE_STALE" in message or "SPIDER_RUN_NOT_OWNED" in message:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=message) from exc
    if "SPIDER_RUN_DELETED" in message:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc
    permanent_conflicts = (
        "SPIDER_ITEM_ID_CONFLICT",
        "SPIDER_PROJECT_CONFLICT",
        "SPIDER_RETENTION_CHANGED",
    )
    if any(code in message for code in permanent_conflicts):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc
    logger.exception("Direct SpiderData 原子写入失败: run_id={}", run_id)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SpiderData persistence unavailable"
    ) from exc


__all__ = ["ingest_direct_spider_items", "ingest_direct_spider_meta"]
