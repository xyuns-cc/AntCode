"""Stream 写入操作（XADD 系列）"""

from typing import Any

from antcode_core.common.serialization import to_json
from antcode_core.infrastructure.redis import crawl_stream_operations as crawl_stream_ops
from antcode_core.infrastructure.redis.stream_records import decode_text
from antcode_core.infrastructure.redis.stream_session import StreamSession


def _serialize_json_fields(data: dict) -> dict:
    """非 str/bytes 字段统一 JSON 序列化后写入 Stream。"""
    return {k: to_json(v) if not isinstance(v, (str, bytes)) else v for k, v in data.items()}


def _trim_kwargs(maxlen: int | None, approximate: bool) -> dict:
    if maxlen is None:
        return {}
    return {"maxlen": maxlen, "approximate": approximate}


class StreamPublishMixin(StreamSession):
    """XADD 及其批量/去重/typed 变体"""

    async def xadd(
        self,
        stream_key: str,
        data: dict,
        *,
        msg_id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        """添加消息到 Stream

        Args:
            stream_key: Stream 键名
            data: 消息数据字典
            msg_id: 消息 ID，默认 "*" 自动生成
            maxlen: 最大长度限制，超过时自动裁剪
            approximate: 是否使用近似裁剪（性能更好）

        Returns:
            消息 ID
        """
        client = await self._get_client()
        serialized = _serialize_json_fields(data)
        result = await client.xadd(stream_key, serialized, id=msg_id, **_trim_kwargs(maxlen, approximate))
        return decode_text(result)

    async def xadd_batch(self, stream_key: str, messages: list[dict], maxlen: int | None = None) -> list[str]:
        """批量添加消息到 Stream（pipeline 提交）

        Args:
            stream_key: Stream 键名
            messages: 消息数据列表
            maxlen: 最大长度限制

        Returns:
            消息 ID 列表
        """
        client = await self._get_client()
        pipe = client.pipeline()
        for data in messages:
            serialized = _serialize_json_fields(data)
            if maxlen:
                pipe.xadd(stream_key, serialized, maxlen=maxlen, approximate=True)
            else:
                pipe.xadd(stream_key, serialized)
        results = await pipe.execute()
        return [decode_text(result) for result in results]

    async def xadd_batch_active(
        self,
        stream_key: str,
        messages: list[dict],
        *,
        deleted_fence_key: str,
    ) -> list[str]:
        """仅当项目未被删除时批量写入（Crawl 删除 fence 由 Lua 原子校验）。"""
        if not messages:
            return []
        client = await self._get_client()
        return await crawl_stream_ops.xadd_batch_active(
            client,
            stream_key,
            messages,
            deleted_fence_key=deleted_fence_key,
        )

    async def xadd_typed(
        self,
        stream_key: str,
        msg: Any,
        *,
        msg_id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        """使用注入的 codec 编码后发布消息

        Args:
            stream_key: Stream 键名
            msg: 业务对象（由 codec.encode 处理）
            msg_id: 消息 ID，默认 "*" 自动生成
            maxlen: 最大长度限制
            approximate: 是否使用近似裁剪

        Returns:
            消息 ID
        """
        client = await self._get_client()
        serialized = self._codec.encode(msg)
        result = await client.xadd(stream_key, serialized, id=msg_id, **_trim_kwargs(maxlen, approximate))
        return decode_text(result)


__all__ = ["StreamPublishMixin"]
