"""消息确认与所有权转移（XACK / XDEL / XCLAIM / XAUTOCLAIM）"""

from typing import Any

from loguru import logger
from redis.exceptions import ResponseError

from antcode_core.infrastructure.redis import crawl_stream_operations as crawl_stream_ops
from antcode_core.infrastructure.redis.stream_records import (
    XAUTOCLAIM_DELETED_INDEX,
    StreamMessage,
    decode_text,
    parse_entries,
)
from antcode_core.infrastructure.redis.stream_session import StreamSession

# XAUTOCLAIM 默认只回收空闲超过 5 分钟的 PEL 消息，单批最多 100 条
DEFAULT_AUTOCLAIM_MIN_IDLE_MS = 300_000
DEFAULT_AUTOCLAIM_COUNT = 100

# Redis 对不存在的 Stream 键执行 XINFO GROUPS 时返回的错误片段（已小写化比较）
MISSING_STREAM_ERROR = "no such key"


class StreamAckError(RuntimeError):
    """XACK 打到了一个不存在的消费者组

    Redis 的 XACK 返回实际从 PEL 移除的条数，0 有两种完全不同的成因：

    1. **消费者组不存在**（组名传错 / 组被销毁）。这是配置或代码缺陷，
       消息会永久滞留 PEL 等着被 XAUTOCLAIM 反复重投——必须显式抛出。
    2. **这批消息已不在该组的 PEL 里**。多副本部署下另一个 consumer 通过
       XAUTOCLAIM 接管并结算了它们，属于正常竞态：本端无事可做，如实返回
       0 即可，不能把它伪装成缺陷让消费循环崩溃退避。

    历史实现把两者压成同一个异常；本类现在只代表第 1 种。
    """


def _group_name(group: Any) -> str:
    """从 XINFO GROUPS 条目里取组名（兼容 decode_responses 开 / 关两种形态）。"""
    if not isinstance(group, dict):
        return ""
    return decode_text(group.get("name", group.get(b"name")))


async def _consumer_group_exists(client: Any, stream_key: str, group_name: str) -> bool:
    """查询消费者组是否存在；Stream 键不存在等价于组不存在。"""
    try:
        groups = await client.xinfo_groups(stream_key)
    except ResponseError as exc:
        if MISSING_STREAM_ERROR not in str(exc).lower():
            raise
        return False
    return any(_group_name(group) == group_name for group in groups or [])


class StreamAckMixin(StreamSession):
    """确认已处理消息，以及超时消息的认领/转移"""

    async def xack(self, stream_key: str, msg_ids: list[str], group_name: str) -> int:
        """确认消息已处理

        Args:
            stream_key: Stream 键名
            msg_ids: 消息 ID 列表
            group_name: 消费者组名称（必填）

        Returns:
            确认成功的消息数量

        Raises:
            StreamAckError: 消费者组不存在（见该异常的文档）
        """
        if not msg_ids:
            return 0

        client = await self._get_client()
        acked = int(await client.xack(stream_key, group_name, *msg_ids))
        if acked == 0:
            await self._explain_zero_acknowledged(client, stream_key, group_name=group_name, msg_ids=msg_ids)
            return 0
        if acked < len(msg_ids):
            logger.error(
                "XACK 部分确认失败: stream={} group={} 请求={} 实际={}",
                stream_key,
                group_name,
                len(msg_ids),
                acked,
            )
        return acked

    @staticmethod
    async def _explain_zero_acknowledged(
        client: Any,
        stream_key: str,
        *,
        group_name: str,
        msg_ids: list[str],
    ) -> None:
        """XACK 返回 0 的两种成因必须分开：组缺失是缺陷，消息已离开 PEL 不是。"""
        if not await _consumer_group_exists(client, stream_key, group_name):
            raise StreamAckError(f"XACK 打到不存在的消费者组: stream={stream_key} group={group_name} msg_ids={msg_ids}")
        logger.warning(
            "XACK 未确认任何消息(这批消息已不在该组 PEL，多半已被其他 consumer 接管结算): "
            "stream={} group={} msg_ids={}",
            stream_key,
            group_name,
            msg_ids,
        )

    async def xack_delete(self, stream_key: str, msg_ids: list[str], group_name: str) -> int:
        """Atomically ACK and delete only IDs currently present in this group's PEL."""
        if not msg_ids:
            return 0
        client = await self._get_client()
        return await crawl_stream_ops.xack_delete(client, stream_key, msg_ids, group_name=group_name)

    async def xdel(self, stream_key: str, msg_ids: list[str]) -> int:
        """删除消息

        Args:
            stream_key: Stream 键名
            msg_ids: 消息 ID 列表

        Returns:
            删除成功的消息数量
        """
        if not msg_ids:
            return 0
        client = await self._get_client()
        return await client.xdel(stream_key, *msg_ids)

    async def xclaim(
        self,
        stream_key: str,
        msg_ids: list[str],
        group_name: str,
        *,
        consumer_name: str = "worker",
        min_idle_time_ms: int = 0,
        retry_count: int | None = None,
    ) -> list[StreamMessage]:
        """转移消息所有权

        Args:
            stream_key: Stream 键名
            msg_ids: 消息 ID 列表
            group_name: 消费者组名称（必填）
            consumer_name: 新的消费者名称
            min_idle_time_ms: 最小空闲时间（毫秒）
            retry_count: 设置重试计数，None 表示不修改

        Returns:
            成功转移的 StreamMessage 列表
        """
        if not msg_ids:
            return []

        client = await self._get_client()
        kwargs = {} if retry_count is None else {"retrycount": retry_count}
        try:
            result = await client.xclaim(stream_key, group_name, consumer_name, min_idle_time_ms, msg_ids, **kwargs)
        except Exception as e:
            logger.error(f"XCLAIM 失败: {stream_key}, 错误: {e}")
            raise
        return parse_entries(result, stream_key)

    async def xautoclaim(
        self,
        stream_key: str,
        group_name: str,
        consumer_name: str = "worker",
        *,
        min_idle_time_ms: int = DEFAULT_AUTOCLAIM_MIN_IDLE_MS,
        start_id: str = "0-0",
        count: int = DEFAULT_AUTOCLAIM_COUNT,
    ) -> tuple[str, list[StreamMessage], list[str]]:
        """自动转移超时消息

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）
            consumer_name: 新的消费者名称
            min_idle_time_ms: 最小空闲时间（毫秒）
            start_id: 起始消息 ID
            count: 最大转移数量

        Returns:
            (next_start_id, [StreamMessage], deleted_ids) 元组
        """
        client = await self._get_client()
        try:
            result = await client.xautoclaim(
                stream_key, group_name, consumer_name, min_idle_time_ms, start_id, count=count
            )
        except Exception as e:
            logger.error(f"XAUTOCLAIM 失败: {stream_key}, 错误: {e}")
            raise

        next_id = decode_text(result[0])
        messages = parse_entries(result[1], stream_key)
        deleted_ids: list[str] = []
        if len(result) > XAUTOCLAIM_DELETED_INDEX:
            deleted_ids = [decode_text(del_id) for del_id in result[XAUTOCLAIM_DELETED_INDEX]]
        return next_id, messages, deleted_ids

    async def move_pending(
        self,
        source_key: str,
        destination_key: str,
        *,
        group_name: str,
        msg_id: str,
        data: dict,
        maxlen: int | None = None,
        expire_seconds: int | None = None,
        deleted_fence_key: str,
    ) -> str | None:
        """写入目标 Stream 并 ACK 源 PEL 消息。

        Crawl source/destination keys share a project hash tag. One Lua script
        performs destination XADD, source XACK/XDEL and destination TTL update.
        """
        if not data:
            raise ValueError("恢复消息数据不能为空")
        client = await self._get_client()
        return await crawl_stream_ops.move_pending(
            client,
            source_key,
            destination_key,
            group_name=group_name,
            msg_id=msg_id,
            data=data,
            maxlen=maxlen,
            expire_seconds=expire_seconds,
            deleted_fence_key=deleted_fence_key,
        )


__all__ = [
    "DEFAULT_AUTOCLAIM_COUNT",
    "DEFAULT_AUTOCLAIM_MIN_IDLE_MS",
    "StreamAckError",
    "StreamAckMixin",
]
