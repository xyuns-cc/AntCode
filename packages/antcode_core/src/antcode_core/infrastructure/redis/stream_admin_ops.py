"""队列状态查询与清理（XLEN / XPENDING / XINFO / XTRIM）"""

from typing import Any

from antcode_core.infrastructure.redis.pending_summary import empty_pending_summary, parse_pending_summary
from antcode_core.infrastructure.redis.stream_records import PendingMessage, decode_text, parse_pending_message
from antcode_core.infrastructure.redis.stream_session import StreamSession

# SCAN 单轮游标步长：过小会放大往返次数，过大会拉长单次阻塞
SCAN_BATCH_SIZE = 1000

# XPENDING 明细默认返回条数上限
DEFAULT_PENDING_RANGE_COUNT = 100


def _decode_info_mapping(mapping: dict) -> dict:
    return {decode_text(k): decode_text(v) if isinstance(v, bytes) else v for k, v in mapping.items()}


class StreamAdminMixin(StreamSession):
    """只读状态查询与 Stream 清理"""

    async def xlen(self, stream_key: str) -> int:
        """获取 Stream 长度"""
        client = await self._get_client()
        return await client.xlen(stream_key)

    async def exists(self, key: str) -> bool:
        client = await self._get_client()
        return bool(await client.exists(key))

    async def scan_keys(self, pattern: str) -> list[str]:
        """扫描匹配的 Redis 键并统一解码。"""
        client = await self._get_client()
        return [decode_text(key) async for key in client.scan_iter(match=pattern, count=SCAN_BATCH_SIZE)]

    async def xpending(self, stream_key: str, group_name: str) -> dict:
        """获取 pending 消息摘要

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）

        Returns:
            {pending_count, min_id, max_id, consumers: {name: count}}
        """
        client = await self._get_client()
        try:
            result = await client.xpending(stream_key, group_name)
        except Exception as e:
            if "NOGROUP" in str(e):
                return empty_pending_summary()
            raise
        return parse_pending_summary(result)

    async def xpending_range(
        self,
        stream_key: str,
        group_name: str,
        *,
        start: str = "-",
        end: str = "+",
        count: int = DEFAULT_PENDING_RANGE_COUNT,
        consumer_name: str | None = None,
    ) -> list[PendingMessage]:
        """获取 pending 消息详情

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）
            start: 起始 ID
            end: 结束 ID
            count: 数量限制
            consumer_name: 指定消费者，None 表示所有

        Returns:
            PendingMessage 列表
        """
        client = await self._get_client()
        kwargs = {} if not consumer_name else {"consumername": consumer_name}
        try:
            result = await client.xpending_range(stream_key, group_name, start, end, count, **kwargs)
        except Exception as e:
            if "NOGROUP" in str(e):
                return []
            raise
        return [parse_pending_message(item) for item in result]

    async def xinfo_stream(self, stream_key: str) -> dict:
        """获取 Stream 信息"""
        client = await self._get_client()
        try:
            result = await client.xinfo_stream(stream_key)
        except Exception as e:
            if "no such key" in str(e).lower():
                return {}
            raise
        return _decode_info_mapping(result)

    async def xinfo_groups(self, stream_key: str) -> list[dict]:
        """获取消费者组信息"""
        client = await self._get_client()
        try:
            result = await client.xinfo_groups(stream_key)
        except Exception as e:
            if "no such key" in str(e).lower():
                return []
            raise
        return [_decode_info_mapping(group_data) for group_data in result]

    async def xtrim(
        self,
        stream_key: str,
        *,
        maxlen: int | None = None,
        approximate: bool = True,
        minid: str | None = None,
    ) -> int:
        """裁剪 Stream

        Args:
            stream_key: Stream 键名
            maxlen: 最大长度（与 minid 二选一）
            approximate: 是否使用近似裁剪
            minid: P1-19：MINID 模式——裁剪所有 msg_id < minid 的消息。
                优先级高于 maxlen；用于按"消费者组已 ACK 游标"安全裁剪，
                避免 MAXLEN 撞未 ACK 消息导致静默丢消息。

        Returns:
            删除的消息数量
        """
        client = await self._get_client()
        if minid is not None:
            try:
                return await client.xtrim(stream_key, minid=minid, approximate=approximate)
            except TypeError as exc:
                raise RuntimeError("当前 redis-py 不支持安全的 XTRIM MINID") from exc
        if maxlen is None:
            raise ValueError("xtrim 需要 maxlen 或 minid 至少一个参数")
        return await client.xtrim(stream_key, maxlen=maxlen, approximate=approximate)

    async def delete_stream(self, stream_key: str) -> bool:
        """删除整个 Stream"""
        client = await self._get_client()
        result: Any = await client.delete(stream_key)
        return bool(result)


__all__ = ["DEFAULT_PENDING_RANGE_COUNT", "SCAN_BATCH_SIZE", "StreamAdminMixin"]
