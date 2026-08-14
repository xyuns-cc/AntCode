"""Worker TaskStatus 在 Gateway 与 Direct 共用的安全合同。"""

from __future__ import annotations

from typing import Any

from antcode_core.common.error_messages import MAX_PERSISTED_ERROR_MESSAGE_BYTES
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD, ProtoCodec

MAX_STATUS_FRAME_BYTES = 1024 * 1024
MAX_STATUS_ERROR_BYTES = MAX_PERSISTED_ERROR_MESSAGE_BYTES


def status_payload_error(task_status: Any) -> str | None:
    if task_status.ByteSize() > MAX_STATUS_FRAME_BYTES:
        return f"status frame 超过 {MAX_STATUS_FRAME_BYTES} bytes"
    if len(task_status.error_message.encode("utf-8")) > MAX_STATUS_ERROR_BYTES:
        return "error_message 超过 16 KiB 上限"
    return None


class BoundedTaskStatusCodec:
    """在 Redis result stream 解码前限制原始 frame，在解码后限制错误字段。"""

    def __init__(self, message_type: type[Any]) -> None:
        self._codec = ProtoCodec(message_type)

    def encode(self, message: Any) -> dict:
        _require_valid_status(message)
        return self._codec.encode(message)

    def decode(self, fields: dict) -> Any:
        raw = fields.get(PROTO_FIELD, fields.get(PROTO_FIELD.decode("utf-8")))
        if not isinstance(raw, bytes) or len(raw) > MAX_STATUS_FRAME_BYTES:
            raise ValueError(f"status Redis frame 超过 {MAX_STATUS_FRAME_BYTES} bytes 或类型非法")
        message = self._codec.decode(fields)
        _require_valid_status(message)
        return message


def _require_valid_status(task_status: Any) -> None:
    if error := status_payload_error(task_status):
        raise ValueError(error)


__all__ = [
    "BoundedTaskStatusCodec",
    "MAX_STATUS_ERROR_BYTES",
    "MAX_STATUS_FRAME_BYTES",
    "status_payload_error",
]
