"""
结果处理器

接收 Worker 的任务执行状态（``TaskStatus``）并以 **Proto bytes** 单字段框架
（``{PROTO_FIELD: bytes}``）写入 Redis Stream，由 Master ``ResultLoop`` 用
``ProtoCodec(data_pb2.TaskStatus)`` 直接解码。

P1c 改造：彻底移除 JSON 落库路径，统一走 Proto bytes，端到端与 P1a Master 对齐。

**Validates: Requirements 6.6**
"""

from __future__ import annotations

from antcode_contracts import data_pb2
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger


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
