"""
结果处理器

接收 Worker 的任务执行状态（``TaskStatus``）并以 **Proto bytes** 单字段框架
（``{PROTO_FIELD: bytes}``）写入 Redis Stream，由 Master ``ResultLoop`` 用
``ProtoCodec(data_pb2.TaskStatus)`` 直接解码。

P1c 改造：彻底移除 JSON 落库路径，统一走 Proto bytes，端到端与 P1a Master 对齐。

**Validates: Requirements 6.6**
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from antcode_contracts import data_pb2
from antcode_contracts.common_pb2 import Timestamp
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger

# Status 字符串 -> data_pb2.Status enum 的映射（用于兼容旧调用方传入字符串）
_STATUS_FROM_STR: dict[str, data_pb2.Status] = {
    "": data_pb2.STATUS_UNSPECIFIED,
    "pending": data_pb2.STATUS_PENDING,
    "running": data_pb2.STATUS_RUNNING,
    "success": data_pb2.STATUS_COMPLETED,
    "completed": data_pb2.STATUS_COMPLETED,
    "failed": data_pb2.STATUS_FAILED,
    "cancelled": data_pb2.STATUS_CANCELLED,
    "canceled": data_pb2.STATUS_CANCELLED,
    "timeout": data_pb2.STATUS_TIMEOUT,
}


def _to_status_enum(status: object) -> data_pb2.Status:
    """把传入的 status（int 或字符串）映射成 ``data_pb2.Status`` 数值。"""
    if isinstance(status, int):
        if status in data_pb2.Status.values():
            return cast(data_pb2.Status, status)
        return data_pb2.STATUS_UNSPECIFIED
    if isinstance(status, str):
        return _STATUS_FROM_STR.get(status.strip().lower(), data_pb2.STATUS_UNSPECIFIED)
    return data_pb2.STATUS_UNSPECIFIED


def _to_timestamp(value: datetime | None) -> Timestamp | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    epoch_seconds = value.timestamp()
    seconds = int(epoch_seconds)
    nanos = int((epoch_seconds - seconds) * 1_000_000_000)
    return Timestamp(seconds=seconds, nanos=nanos)


class ResultHandler:
    """结果处理器

    把 Worker 上报的 ``TaskStatus`` 以 Proto bytes 写入 Redis Stream。
    Stream 上的消息形如 ``{PROTO_FIELD: TaskStatus.SerializeToString()}``，
    与 Master ``ResultLoop`` 的 ``ProtoCodec(TaskStatus)`` 解码端对齐。
    """

    def __init__(
        self,
        stream: StreamClient | None = None,
        result_stream: str | None = None,
    ):
        """初始化处理器

        Args:
            stream: 注入测试用的 ``StreamClient``；默认创建带
                ``ProtoCodec(TaskStatus)`` 的实例
            result_stream: Stream 键名，默认使用 ``task_result_stream()``
        """
        self._stream = stream or StreamClient(codec=ProtoCodec(data_pb2.TaskStatus))
        self._result_stream = result_stream or task_result_stream()

    async def handle(self, task_status: data_pb2.TaskStatus) -> bool:
        """以 Proto bytes 写入 result stream。"""
        try:
            task_status.error_message = normalize_persisted_error_message(task_status.error_message) or ""
            await self._stream.xadd_typed(self._result_stream, task_status)
            logger.info(
                "结果已写入 stream: run_id={} task_id={} status={}",
                task_status.run_id,
                task_status.task_id,
                int(task_status.status),
            )
            return True
        except Exception as exc:
            logger.exception(f"写入结果流失败: {exc}")
            return False

    async def handle_status_update(
        self,
        run_id: str,
        task_id: str = "",
        status: object = "",
        exit_code: int | None = None,
        error_message: str | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """处理中间状态更新（如 running）。

        构造 ``TaskStatus`` proto 后调用 ``handle`` 落 Stream。
        """
        msg = data_pb2.TaskStatus(
            run_id=run_id,
            task_id=task_id or run_id,
            status=_to_status_enum(status),
            exit_code=int(exit_code) if exit_code is not None else 0,
            error_message=normalize_persisted_error_message(error_message) or "",
        )
        ts = _to_timestamp(timestamp or datetime.now(UTC))
        if ts is not None:
            msg.started_at.CopyFrom(ts)
        return await self.handle(msg)
