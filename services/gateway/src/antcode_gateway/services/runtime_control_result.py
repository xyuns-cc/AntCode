"""Validate and persist authenticated runtime-control settlements."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.infrastructure.redis import (
    control_global_stream,
    control_reply_stream,
    decode_stream_payload,
)
from redis.exceptions import ResponseError

from antcode_gateway.services.control_stream_ownership import ControlPendingOwnerError
from antcode_gateway.services.runtime_control_marker import (
    build_settlement_evidence,
)
from antcode_gateway.services.runtime_control_marker import (
    canonical_data_json as _canonical_data_json,
)
from antcode_gateway.services.runtime_control_settlement_store import (
    persist_fenced_settlement_marker,
    settlement_key,
)

CONTROL_REPLY_TTL_SECONDS = 120
CONTROL_REPLY_MAXLEN = 1
CONTROL_SETTLEMENT_TTL_SECONDS = 1_200
MAX_CONTROL_RESULT_BYTES = 1024 * 1024
MAX_CONTROL_ERROR_BYTES = 16 * 1024
_CONTROL_TYPES = {"cancel", "kill", "config_update", "ping", "runtime_manage"}


class ControlEventGoneError(Exception):
    """控制事件已从 Stream / PEL 消失（通常是 ACK 已生效后的幂等重放）。

    与协议 / 归属违规区分：at-least-once 重试撞上已结算并被裁剪的事件属于良性重复，
    调用方应返回 ``received=False`` + OK 状态，而不是 gRPC 错误。
    """


async def is_committed_runtime_result(
    redis: Any,
    *,
    event_id: str,
    worker_id: str,
    request: Any,
) -> bool:
    """Return true only for an identical, previously persisted settlement."""
    if not request.request_id:
        return False
    if event_id.rsplit("|", 1)[0] == control_global_stream():
        raise ValueError("全局控制事件禁止提交运行时结果")
    _validate_result_bounds(request)
    fingerprint = _settlement_fingerprint(event_id, worker_id, request)
    stored = await redis.get(settlement_key(event_id))
    if stored is None:
        return False
    evidence = _result_evidence(
        event_id=event_id,
        worker_id=worker_id,
        request=request,
        fingerprint=fingerprint,
    )
    if _text(stored) not in {fingerprint, evidence.encode()}:
        raise ValueError("控制事件已使用不同结果完成")
    await persist_fenced_settlement_marker(
        redis,
        event_id=event_id,
        worker_id=worker_id,
        lease_id=request.lease_id,
        fingerprint=fingerprint,
        marker=evidence.encode(),
        ttl_seconds=CONTROL_SETTLEMENT_TTL_SECONDS,
    )
    _, message_id = event_id.rsplit("|", 1)
    await _persist_reply_once(
        redis,
        reply_stream=control_reply_stream(request.request_id),
        message_id=message_id,
        payload=_reply_payload(request, fingerprint),
    )
    return True


async def validate_control_ack(
    redis: Any,
    *,
    stream_key: str,
    message_id: str,
    request: Any,
) -> tuple[dict[str, Any], bool]:
    """Validate AckControl fields against the immutable source event."""
    decoded = await _load_stream_entry(redis, stream_key, message_id)
    control_type = str(decoded.get("control_type") or "")
    if control_type not in _CONTROL_TYPES:
        raise ValueError("原控制事件类型无效")
    if control_type != "runtime_manage":
        if request.request_id or request.data_json:
            raise ValueError("普通控制 ACK 不得携带运行时结果")
        return decoded, False
    if stream_key == control_global_stream():
        raise ValueError("全局控制事件禁止提交运行时结果")

    expected_request_id = str(decoded.get("request_id") or "")
    if not expected_request_id or request.request_id != expected_request_id:
        raise ValueError("运行时结果 request_id 与原控制事件不匹配")
    if not request.data_json:
        raise ValueError("运行时结果 data_json 不能为空；无数据必须使用 null")
    _validate_result_bounds(request)
    _canonical_data_json(request.data_json)
    expected_reply = control_reply_stream(expected_request_id)
    if str(decoded.get("reply_stream") or "") != expected_reply:
        raise ValueError("原控制事件 reply_stream 非法")
    return decoded, True


async def require_pending_owner(
    redis: Any,
    *,
    stream_key: str,
    group: str,
    message_id: str,
    consumer_name: str,
) -> None:
    """Require the authenticated lease generation to own this pending event."""
    pending = await redis.xpending_range(
        stream_key,
        group,
        min=message_id,
        max=message_id,
        count=1,
    )
    if not pending:
        raise ControlEventGoneError("控制事件已不在待确认队列（可能已确认）")
    item = pending[0]
    if _text(item.get("message_id")) != message_id:
        raise ControlPendingOwnerError("控制事件 PEL 归属校验失败")
    if _text(item.get("consumer")) != consumer_name:
        raise ControlPendingOwnerError("控制事件不属于当前 Worker 租约代际的待确认队列")


async def publish_runtime_control_result(
    redis: Any,
    *,
    event_id: str,
    worker_id: str,
    message_id: str,
    decoded: dict[str, Any],
    request: Any,
) -> None:
    """Persist one canonical reply and an idempotency marker before XACK."""
    reply_stream = control_reply_stream(str(decoded["request_id"]))
    fingerprint = _settlement_fingerprint(event_id, worker_id, request)
    payload = _reply_payload(request, fingerprint)
    existing = await _load_optional_stream_entry(redis, reply_stream, message_id)
    if existing is not None:
        _require_same_reply(existing, payload)
    marker = _result_evidence(
        event_id=event_id,
        worker_id=worker_id,
        request=request,
        fingerprint=fingerprint,
    ).encode()
    await persist_fenced_settlement_marker(
        redis,
        event_id=event_id,
        worker_id=worker_id,
        lease_id=request.lease_id,
        fingerprint=fingerprint,
        marker=marker,
        ttl_seconds=CONTROL_SETTLEMENT_TTL_SECONDS,
    )
    await _persist_reply_once(
        redis,
        reply_stream=reply_stream,
        message_id=message_id,
        payload=payload,
    )


async def _persist_reply_once(
    redis: Any,
    *,
    reply_stream: str,
    message_id: str,
    payload: dict[str, str],
) -> None:
    existing = await _load_optional_stream_entry(redis, reply_stream, message_id)
    if existing is not None:
        _require_same_reply(existing, payload)
        if not await redis.expire(reply_stream, CONTROL_REPLY_TTL_SECONDS):
            raise RuntimeError("运行时控制结果 TTL 更新失败")
        return

    try:
        async with redis.pipeline(transaction=True) as pipeline:
            pipeline.xadd(
                reply_stream,
                payload,
                id=message_id,
                maxlen=CONTROL_REPLY_MAXLEN,
                approximate=False,
            )
            pipeline.expire(reply_stream, CONTROL_REPLY_TTL_SECONDS)
            results = await pipeline.execute()
    except ResponseError:
        concurrent = await _load_optional_stream_entry(redis, reply_stream, message_id)
        if concurrent is None:
            raise
        _require_same_reply(concurrent, payload)
        return
    if not results[0] or not results[1]:
        raise RuntimeError("运行时控制结果持久化失败")


async def _load_stream_entry(redis: Any, stream_key: str, message_id: str) -> dict[str, Any]:
    decoded = await _load_optional_stream_entry(redis, stream_key, message_id)
    if decoded is None:
        # Stream 条目在 ACK 结算后会被裁剪：重复 ACK 命中这里属于良性重放。
        raise ControlEventGoneError("原控制事件不存在（可能已结算并裁剪）")
    return decoded


async def _load_optional_stream_entry(
    redis: Any,
    stream_key: str,
    message_id: str,
) -> dict[str, Any] | None:
    entries = await redis.xrange(stream_key, min=message_id, max=message_id, count=1)
    if not entries:
        return None
    stored_message_id, raw = entries[0]
    if _text(stored_message_id) != message_id:
        raise ValueError("Redis Stream 事件 ID 不匹配")
    return decode_stream_payload(raw)


def _reply_payload(request: Any, fingerprint: str) -> dict[str, str]:
    return {
        "request_id": request.request_id,
        "success": str(bool(request.success)).lower(),
        "data": _canonical_data_json(request.data_json),
        "error": _safe_control_error(request.error),
        "settlement": fingerprint,
    }


def _result_evidence(
    *,
    event_id: str,
    worker_id: str,
    request: Any,
    fingerprint: str,
):
    return build_settlement_evidence(
        event_id=event_id,
        worker_id=worker_id,
        request=request,
        fingerprint=fingerprint,
        canonical_data_json=_canonical_data_json(request.data_json),
    )


def _require_same_reply(existing: dict[str, Any], expected: dict[str, str]) -> None:
    actual = {key: _stored_reply_value(key, existing.get(key)) for key in expected}
    if actual != expected:
        raise ValueError("运行时控制结果与已持久化结果冲突")


def _stored_reply_value(key: str, value: Any) -> str:
    if key != "data":
        return str(value or "")
    if isinstance(value, str):
        return _canonical_data_json(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _settlement_fingerprint(event_id: str, worker_id: str, request: Any) -> str:
    canonical = json.dumps(
        {
            "data": json.loads(_canonical_data_json(request.data_json)),
            "error": _safe_control_error(request.error),
            "event_id": event_id,
            "lease_id": request.lease_id,
            "request_id": request.request_id,
            "success": bool(request.success),
            "worker_id": worker_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_result_bounds(request: Any) -> None:
    if len(request.data_json.encode("utf-8")) > MAX_CONTROL_RESULT_BYTES:
        raise ValueError("运行时结果 data_json 超过 1 MiB 上限")
    if len((request.error or "").encode("utf-8")) > MAX_CONTROL_ERROR_BYTES:
        raise ValueError("运行时结果 error 超过 16 KiB 上限")


def _safe_control_error(error: object | None) -> str:
    return normalize_persisted_error_message(error, max_bytes=MAX_CONTROL_ERROR_BYTES) or ""


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)
