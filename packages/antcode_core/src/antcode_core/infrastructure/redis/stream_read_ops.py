"""消费者组读取（XREADGROUP 系列）

``group_name`` 为必填参数，杜绝历史默认组名带来的静默错组读取。
"""

from typing import Any

from loguru import logger

from antcode_core.infrastructure.redis.stream_group_ops import StreamGroupMixin
from antcode_core.infrastructure.redis.stream_records import (
    StreamMessage,
    TypedStreamMessage,
    parse_typed_result,
    parse_typed_result_multi,
    parse_xread_result,
    parse_xread_result_multi,
)

# ">" 读取尚未投递的新消息，"0" 重读本消费者自己的 PEL
NEW_MESSAGES_ID = ">"
PENDING_MESSAGES_ID = "0"

# 单次 XREADGROUP 默认批量
DEFAULT_READ_COUNT = 10


def _read_kwargs(count: int, block_ms: int | None) -> dict:
    kwargs: dict[str, Any] = {"count": count}
    if block_ms is not None:
        kwargs["block"] = block_ms
    return kwargs


class StreamReadMixin(StreamGroupMixin):
    """单/多 Stream 的 dict 读取与 typed 读取"""

    async def _read_single(
        self,
        *,
        stream_key: str,
        group_name: str,
        consumer_name: str,
        count: int,
        block_ms: int | None,
        read_pending: bool,
    ) -> Any:
        """执行一次单 Stream XREADGROUP；组不存在时补建并返回 None。"""
        client = await self._get_client()
        msg_id = PENDING_MESSAGES_ID if read_pending else NEW_MESSAGES_ID
        try:
            return await client.xreadgroup(
                group_name,
                consumer_name,
                streams={stream_key: msg_id},
                **_read_kwargs(count, block_ms),
            )
        except Exception as e:
            if "NOGROUP" not in str(e):
                raise
            await self.ensure_group(stream_key, group_name)
            return None

    async def _read_multi(
        self,
        *,
        stream_keys: list[str],
        group_name: str,
        consumer_name: str,
        count: int,
        block_ms: int | None,
    ) -> Any:
        """执行一次多 Stream XREADGROUP，读取前确保所有组已存在。"""
        client = await self._get_client()
        for key in stream_keys:
            await self.ensure_group(key, group_name)
        streams = dict.fromkeys(stream_keys, NEW_MESSAGES_ID)
        try:
            return await client.xreadgroup(
                group_name,
                consumer_name,
                streams=streams,
                **_read_kwargs(count, block_ms),
            )
        except Exception as e:
            logger.error(f"多 Stream 读取失败: {e}")
            raise

    async def xreadgroup(
        self,
        stream_key: str,
        group_name: str,
        consumer_name: str = "worker",
        *,
        count: int = DEFAULT_READ_COUNT,
        block_ms: int | None = None,
        read_pending: bool = False,
    ) -> list[StreamMessage]:
        """从消费者组读取消息

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）
            consumer_name: 消费者名称
            count: 读取数量
            block_ms: 阻塞等待毫秒数，None 表示不阻塞
            read_pending: 是否读取 pending 消息（使用 "0" 而非 ">"）

        Returns:
            StreamMessage 列表
        """
        result = await self._read_single(
            stream_key=stream_key,
            group_name=group_name,
            consumer_name=consumer_name,
            count=count,
            block_ms=block_ms,
            read_pending=read_pending,
        )
        return parse_xread_result(result)

    async def xreadgroup_multi(
        self,
        stream_keys: list[str],
        group_name: str,
        consumer_name: str = "worker",
        *,
        count: int = DEFAULT_READ_COUNT,
        block_ms: int | None = None,
    ) -> dict[str, list[StreamMessage]]:
        """从多个 Stream 读取消息（按优先级）

        Args:
            stream_keys: Stream 键名列表（按优先级排序）
            group_name: 消费者组名称（必填）
            consumer_name: 消费者名称
            count: 每个 Stream 读取数量
            block_ms: 阻塞等待毫秒数

        Returns:
            {stream_key: [StreamMessage]} 字典
        """
        result = await self._read_multi(
            stream_keys=stream_keys,
            group_name=group_name,
            consumer_name=consumer_name,
            count=count,
            block_ms=block_ms,
        )
        return parse_xread_result_multi(result)

    async def xreadgroup_typed(
        self,
        stream_key: str,
        group_name: str,
        consumer_name: str = "worker",
        *,
        count: int = DEFAULT_READ_COUNT,
        block_ms: int | None = None,
        read_pending: bool = False,
    ) -> list[TypedStreamMessage]:
        """使用注入的 codec 解码后读取消息

        与 ``xreadgroup`` 接口对齐，但每条消息的 ``payload`` 已经被
        ``codec.decode`` 解码为业务对象；解码失败的消息带
        ``decode_error`` 返回，由调用方转死信队列。
        """
        result = await self._read_single(
            stream_key=stream_key,
            group_name=group_name,
            consumer_name=consumer_name,
            count=count,
            block_ms=block_ms,
            read_pending=read_pending,
        )
        return parse_typed_result(result, self._codec)

    async def xreadgroup_multi_typed(
        self,
        stream_keys: list[str],
        group_name: str,
        consumer_name: str = "worker",
        *,
        count: int = DEFAULT_READ_COUNT,
        block_ms: int | None = None,
    ) -> dict[str, list[TypedStreamMessage]]:
        """多 Stream 版本的 ``xreadgroup_typed``。"""
        result = await self._read_multi(
            stream_keys=stream_keys,
            group_name=group_name,
            consumer_name=consumer_name,
            count=count,
            block_ms=block_ms,
        )
        return parse_typed_result_multi(result, self._codec)


__all__ = ["StreamReadMixin"]
