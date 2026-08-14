"""control stream 条目 → 类型化 ``ControlEvent`` 的解码与构建。

纯函数集合：只看 Redis 条目自身的 payload，不碰租约 fencing、PEL 归属与
投递语义（那些留在 ``GatewayControlService`` 里）。构建失败一律返回 ``None``，
由调用方按毒消息 ACK 丢弃；``runtime_manage`` 的非法截止时间仍按原样抛出，
让上游走毒消息路径而不是静默投递一个无期限的控制指令。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from antcode_contracts import control_pb2
from antcode_contracts.common_pb2 import Timestamp
from antcode_core.infrastructure.redis import decode_stream_payload
from loguru import logger

from antcode_gateway.services.runtime_control_expiry import build_runtime_control_event

_NANOS_PER_SECOND = 1_000_000_000


def build_control_event(
    *,
    stream_key: str,
    msg_id: str,
    data: dict,
) -> control_pb2.ControlEvent | None:
    """Decode one Redis control entry and dispatch to its typed builder."""
    try:
        decoded = decode_stream_payload(data)
    except Exception as exc:
        logger.exception(f"control stream payload 解码失败: {exc}")
        return None
    control_type = decoded.get("control_type", "")
    builder = CONTROL_EVENT_BUILDERS.get(control_type)
    if builder is None:
        logger.warning(f"未识别的 control_type: stream={stream_key} msg_id={msg_id} control_type={control_type}")
        return None
    return builder(
        event_id=f"{stream_key}|{msg_id}",
        stream_key=stream_key,
        msg_id=msg_id,
        decoded=decoded,
    )


def _build_task_cancel_event(*, event_id: str, decoded: dict, **_kwargs) -> control_pb2.ControlEvent:
    return control_pb2.ControlEvent(
        event_id=event_id,
        task_cancel=control_pb2.TaskCancel(
            task_id=str(decoded.get("task_id", "")),
            run_id=str(decoded.get("run_id", "")),
            reason=str(decoded.get("reason", "")),
        ),
    )


def _build_config_update_event(*, event_id: str, decoded: dict, **_kwargs) -> control_pb2.ControlEvent | None:
    config = decoded.get("config")
    if not isinstance(config, dict):
        # 非 dict（缺字段 / null / 旧写入端的空字符串 / 数组）都是畸形 payload。
        # 此前会被改写成一条"看起来合法"的空 ConfigUpdate 投给 Worker，绕开
        # 毒消息 ACK 丢弃路径；现在按模块约定返回 None。
        logger.warning(f"config_update 的 config 字段非法: event_id={event_id} type={type(config).__name__}")
        return None
    return control_pb2.ControlEvent(
        event_id=event_id,
        config_update=control_pb2.ConfigUpdate(config={str(k): str(v) for k, v in config.items()}),
    )


def _build_ping_event(*, event_id: str, **_kwargs) -> control_pb2.ControlEvent:
    now = time.time()
    return control_pb2.ControlEvent(
        event_id=event_id,
        ping=control_pb2.Ping(
            timestamp=Timestamp(seconds=int(now), nanos=int((now - int(now)) * _NANOS_PER_SECOND)),
        ),
    )


CONTROL_EVENT_BUILDERS: dict[str, Callable[..., control_pb2.ControlEvent | None]] = {
    "cancel": _build_task_cancel_event,
    "kill": _build_task_cancel_event,
    "config_update": _build_config_update_event,
    "runtime_manage": build_runtime_control_event,
    "ping": _build_ping_event,
}


__all__ = ["CONTROL_EVENT_BUILDERS", "build_control_event"]
