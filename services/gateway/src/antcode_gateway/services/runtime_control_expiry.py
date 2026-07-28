"""Gateway runtime control 事件构建与中继侧过期结算（P1-DR-04）。

从 ``control_service`` 拆出：Gateway worker 无 Redis，无法自行用权威
时钟判定过期；由 Gateway（与 Master 生成 deadline 同一 Redis 时钟）在
投递前过滤。发起方 ``send_command`` 的 xread 早已超时返回失败，此处
ACK+trim 是终局。
"""

from __future__ import annotations

import json
from typing import Any

from antcode_contracts import control_pb2
from antcode_core.application.services.runtime.runtime_control_service import redis_server_now_ms
from antcode_core.infrastructure.redis import control_global_stream
from loguru import logger


async def settle_expired_runtime_control(
    redis,
    *,
    worker_id: str,
    stream_key: str,
    message_id: str,
    event: control_pb2.ControlEvent,
    group: str,
    trim,
) -> bool:
    """已过期的 runtime 指令在中继侧按 Redis TIME 结算丢弃（P1-DR-04）。"""
    if event.WhichOneof("payload") != "runtime_control":
        return False
    expires_at_ms = int(event.runtime_control.expires_at_ms or 0)
    if expires_at_ms <= 0:
        return False
    if await redis_server_now_ms(redis) < expires_at_ms:
        return False
    await redis.xack(stream_key, group, message_id)
    await trim(redis, stream_key, message_id, group=group)
    logger.warning(
        "丢弃已过期的 runtime control(中继侧结算): worker={} stream={} msg_id={} request_id={}",
        worker_id,
        stream_key,
        message_id,
        event.runtime_control.request_id,
    )
    return True


def build_runtime_control_event(
    *,
    event_id: str,
    stream_key: str,
    msg_id: str,
    decoded: dict,
) -> control_pb2.ControlEvent | None:
    """Redis control entry → typed ``RuntimeControl`` 事件（拒绝 global 广播）。"""
    if stream_key == control_global_stream():
        logger.error(
            "拒绝 global runtime_manage: stream={} msg_id={} request_id={}",
            stream_key,
            msg_id,
            decoded.get("request_id", ""),
        )
        return None
    reply_stream = decoded.get("reply_stream")
    params = {"reply_stream": str(reply_stream)} if reply_stream else {}
    action = str(decoded.get("action", ""))
    runtime_control = control_pb2.RuntimeControl(
        request_id=str(decoded.get("request_id", "")),
        action=action,
        expires_at_ms=int(decoded.get("expires_at_ms") or 0),
        params=params,
        action_typed=control_pb2.RuntimeAction(
            generic=control_pb2.GenericAction(name=action, args=_runtime_control_args(decoded)),
        ),
    )
    return control_pb2.ControlEvent(event_id=event_id, runtime_control=runtime_control)


def _runtime_control_args(decoded: dict) -> dict[str, str]:
    payload = decoded.get("payload")
    if not isinstance(payload, dict):
        return {}
    return {str(key): _encode_runtime_arg(value) for key, value in payload.items()}


def _encode_runtime_arg(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


__all__ = ["build_runtime_control_event", "settle_expired_runtime_control"]
