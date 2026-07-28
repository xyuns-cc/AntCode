"""Recover committed Gateway runtime-control results before redelivery."""

from __future__ import annotations

from typing import Any

from antcode_core.infrastructure.redis import (
    control_global_stream,
    control_reply_stream,
    decode_stream_payload,
)

from antcode_gateway.services.runtime_control_marker import (
    decode_settlement_evidence,
    require_matching_evidence,
)
from antcode_gateway.services.runtime_control_result import (
    CONTROL_SETTLEMENT_TTL_SECONDS,
    _load_optional_stream_entry,
    _persist_reply_once,
    _text,
)
from antcode_gateway.services.runtime_control_settlement_store import settlement_key


async def recover_committed_runtime_result(
    redis: Any,
    *,
    event_id: str,
    worker_id: str,
    message_id: str,
    raw_event: dict[str, Any],
) -> bool:
    """Repair a committed reply and prevent runtime action redelivery."""
    decoded = decode_stream_payload(raw_event)
    if str(decoded.get("control_type") or "") != "runtime_manage":
        return False
    if event_id.rsplit("|", 1)[0] == control_global_stream():
        return False
    marker = await redis.get(settlement_key(event_id))
    if marker is None:
        return False
    request_id, reply_stream = _require_source_identity(decoded)
    evidence = decode_settlement_evidence(marker)
    if evidence is None:
        await _require_legacy_recovery_reply(
            redis,
            reply_stream=reply_stream,
            message_id=message_id,
            request_id=request_id,
            marker=marker,
        )
    else:
        require_matching_evidence(
            evidence,
            event_id=event_id,
            worker_id=worker_id,
            request_id=request_id,
        )
        await _persist_reply_once(
            redis,
            reply_stream=reply_stream,
            message_id=message_id,
            payload=evidence.reply_payload(),
        )
    if not await redis.expire(settlement_key(event_id), CONTROL_SETTLEMENT_TTL_SECONDS):
        raise RuntimeError("运行时控制 settlement TTL 更新失败")
    return True


def _require_source_identity(decoded: dict[str, Any]) -> tuple[str, str]:
    request_id = str(decoded.get("request_id") or "")
    if not request_id:
        raise ValueError("运行时控制原事件缺少 request_id")
    reply_stream = control_reply_stream(request_id)
    if str(decoded.get("reply_stream") or "") != reply_stream:
        raise ValueError("运行时控制原事件 reply_stream 非法")
    return request_id, reply_stream


async def _require_legacy_recovery_reply(
    redis: Any,
    *,
    reply_stream: str,
    message_id: str,
    request_id: str,
    marker: Any,
) -> None:
    reply = await _load_optional_stream_entry(redis, reply_stream, message_id)
    if reply is None:
        raise RuntimeError("运行时 settlement marker 存在但 reply 缺失")
    if _text(reply.get("request_id")) != request_id:
        raise ValueError("运行时控制结果 request_id 与 settlement marker 不匹配")
    if _text(reply.get("settlement")) != _text(marker):
        raise ValueError("运行时控制结果与 settlement marker 不匹配")
