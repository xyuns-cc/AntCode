"""Redis Streams 封装

提供 Redis Streams 的高级操作封装，支持：
- 消息发布 (XADD)
- 消费者组管理 (XGROUP)
- 消息读取 (XREADGROUP)
- 消息确认 (XACK)
- 超时任务转移 (XCLAIM/XAUTOCLAIM)
- 队列信息查询 (XLEN/XINFO/XPENDING)
"""

from dataclasses import dataclass, field
import time
from typing import Any

import ujson
from loguru import logger
from redis.exceptions import ConnectionError, TimeoutError

from antcode_core.infrastructure.redis.client import get_redis_client


def _to_json(obj: Any) -> str:
    """序列化为 JSON"""
    return ujson.dumps(obj)


def _from_json(data: str | bytes) -> Any:
    """从 JSON 反序列化"""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return ujson.loads(data)


@dataclass
class StreamMessage:
    """Stream 消息数据类"""

    msg_id: str = ""
    data: dict = field(default_factory=dict)
    stream_key: str = ""


@dataclass
class PendingMessage:
    """待处理消息信息"""

    msg_id: str = ""
    consumer: str = ""
    idle_time_ms: int = 0
    delivery_count: int = 0


class StreamClient:
    """Redis Streams 客户端

    封装 Redis Streams 的常用操作，提供：
    - 消息发布和读取
    - 消费者组管理
    - 超时任务回收
    - 队列状态查询
    """

    # 默认消费者组名称
    DEFAULT_GROUP = "default_workers"

    def __init__(self, redis_client=None):
        """初始化 Stream 客户端

        Args:
            redis_client: Redis 客户端实例，为 None 时自动获取
        """
        self._redis = redis_client
        self._last_health_check = 0.0
        self._health_check_interval = 5.0

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is not None:
            now = time.monotonic()
            if now - self._last_health_check >= self._health_check_interval:
                try:
                    await self._redis.ping()
                    self._last_health_check = now
                    return self._redis
                except Exception:
                    self._redis = None

        if self._redis is None:
            self._redis = await get_redis_client()
            self._last_health_check = time.monotonic()
        return self._redis

    def _is_connection_error(self, exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        return "Connection closed" in str(exc)

    # =========================================================================
    # 消息发布
    # =========================================================================

    async def xadd(
        self,
        stream_key: str,
        data: dict,
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

        # 序列化数据为 JSON 字符串
        serialized = {
            k: _to_json(v) if not isinstance(v, (str, bytes)) else v
            for k, v in data.items()
        }

        kwargs = {}
        if maxlen is not None:
            kwargs["maxlen"] = maxlen
            kwargs["approximate"] = approximate

        try:
            result = await client.xadd(stream_key, serialized, id=msg_id, **kwargs)
        except Exception as e:
            if self._is_connection_error(e):
                self._redis = None
                client = await self._get_client()
                result = await client.xadd(stream_key, serialized, id=msg_id, **kwargs)
            else:
                raise

        if isinstance(result, bytes):
            return result.decode("utf-8")
        return result

    async def xadd_batch(
        self,
        stream_key: str,
        messages: list[dict],
        maxlen: int | None = None,
    ) -> list[str]:
        """批量添加消息到 Stream

        Args:
            stream_key: Stream 键名
            messages: 消息数据列表
            maxlen: 最大长度限制

        Returns:
            消息 ID 列表
        """
        client = await self._get_client()
        msg_ids = []

        # 使用 pipeline 批量执行
        pipe = client.pipeline()
        for data in messages:
            serialized = {
                k: _to_json(v) if not isinstance(v, (str, bytes)) else v
                for k, v in data.items()
            }
            if maxlen:
                pipe.xadd(stream_key, serialized, maxlen=maxlen, approximate=True)
            else:
                pipe.xadd(stream_key, serialized)

        results = await pipe.execute()

        for result in results:
            if isinstance(result, bytes):
                msg_ids.append(result.decode("utf-8"))
            else:
                msg_ids.append(result)

        return msg_ids

    # =========================================================================
    # 消费者组管理
    # =========================================================================

    async def xgroup_create(
        self,
        stream_key: str,
        group_name: str | None = None,
        start_id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        """创建消费者组

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称，默认使用 DEFAULT_GROUP
            start_id: 起始消息 ID，"0" 从头开始，"$" 从最新开始
            mkstream: 如果 Stream 不存在是否自动创建

        Returns:
            是否创建成功
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            await client.xgroup_create(stream_key, group, id=start_id, mkstream=mkstream)
            logger.debug(f"创建消费者组成功: {stream_key} -> {group}")
            return True
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"消费者组已存在: {stream_key} -> {group}")
                return True
            logger.error(f"创建消费者组失败: {stream_key} -> {group}, 错误: {e}")
            raise

    async def xgroup_destroy(self, stream_key: str, group_name: str | None = None) -> bool:
        """删除消费者组"""
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            result = await client.xgroup_destroy(stream_key, group)
            logger.debug(f"删除消费者组: {stream_key} -> {group}, 结果: {result}")
            return bool(result)
        except Exception as e:
            logger.error(f"删除消费者组失败: {stream_key} -> {group}, 错误: {e}")
            return False

    async def ensure_group(self, stream_key: str, group_name: str | None = None) -> bool:
        """确保消费者组存在"""
        return await self.xgroup_create(stream_key, group_name, mkstream=True)

    # =========================================================================
    # 消息读取
    # =========================================================================

    async def xreadgroup(
        self,
        stream_key: str,
        group_name: str | None = None,
        consumer_name: str = "worker",
        count: int = 10,
        block_ms: int | None = None,
        read_pending: bool = False,
    ) -> list[StreamMessage]:
        """从消费者组读取消息

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称
            consumer_name: 消费者名称
            count: 读取数量
            block_ms: 阻塞等待毫秒数，None 表示不阻塞
            read_pending: 是否读取 pending 消息

        Returns:
            StreamMessage 列表
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        msg_id = "0" if read_pending else ">"

        kwargs = {"count": count}
        if block_ms is not None:
            kwargs["block"] = block_ms

        try:
            result = await client.xreadgroup(
                group, consumer_name, streams={stream_key: msg_id}, **kwargs
            )
            return self._parse_xread_result(result, stream_key)
        except Exception as e:
            if "NOGROUP" in str(e):
                await self.ensure_group(stream_key, group)
                return []
            if self._is_connection_error(e):
                self._redis = None
                client = await self._get_client()
                result = await client.xreadgroup(
                    group, consumer_name, streams={stream_key: msg_id}, **kwargs
                )
                return self._parse_xread_result(result, stream_key)
            raise

    def _parse_xread_result(self, result, stream_key: str) -> list[StreamMessage]:
        """解析 XREAD/XREADGROUP 结果"""
        messages = []

        if not result:
            return messages

        for stream_data in result:
            if len(stream_data) < 2:
                continue

            stream_name = stream_data[0]
            if isinstance(stream_name, bytes):
                stream_name = stream_name.decode("utf-8")

            for msg_data in stream_data[1]:
                if len(msg_data) < 2:
                    continue

                msg_id = msg_data[0]
                if isinstance(msg_id, bytes):
                    msg_id = msg_id.decode("utf-8")

                data = self._decode_message_data(msg_data[1])

                messages.append(
                    StreamMessage(msg_id=msg_id, data=data, stream_key=stream_name)
                )

        return messages

    def _decode_message_data(self, raw_data) -> dict:
        """解码消息数据"""
        data = {}

        if isinstance(raw_data, dict):
            for k, v in raw_data.items():
                key = k.decode("utf-8") if isinstance(k, bytes) else k
                value = v.decode("utf-8") if isinstance(v, bytes) else v

                try:
                    data[key] = _from_json(value)
                except Exception:
                    data[key] = value

        return data

    # =========================================================================
    # 消息确认
    # =========================================================================

    async def xack(
        self, stream_key: str, msg_ids: list[str], group_name: str | None = None
    ) -> int:
        """确认消息已处理

        Args:
            stream_key: Stream 键名
            msg_ids: 消息 ID 列表
            group_name: 消费者组名称

        Returns:
            确认成功的消息数量
        """
        if not msg_ids:
            return 0

        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            result = await client.xack(stream_key, group, *msg_ids)
            return result
        except Exception as e:
            if self._is_connection_error(e):
                self._redis = None
                client = await self._get_client()
                return await client.xack(stream_key, group, *msg_ids)
            raise

    async def xdel(self, stream_key: str, msg_ids: list[str]) -> int:
        """删除消息"""
        if not msg_ids:
            return 0

        client = await self._get_client()
        result = await client.xdel(stream_key, *msg_ids)
        return result

    # =========================================================================
    # 超时任务转移
    # =========================================================================

    async def xautoclaim(
        self,
        stream_key: str,
        group_name: str | None = None,
        consumer_name: str = "worker",
        min_idle_time_ms: int = 300000,
        start_id: str = "0-0",
        count: int = 100,
    ) -> tuple[str, list[StreamMessage], list[str]]:
        """自动转移超时消息

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称
            consumer_name: 新的消费者名称
            min_idle_time_ms: 最小空闲时间（毫秒），默认 5 分钟
            start_id: 起始消息 ID
            count: 最大转移数量

        Returns:
            (next_start_id, [StreamMessage], deleted_ids) 元组
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            result = await client.xautoclaim(
                stream_key, group, consumer_name, min_idle_time_ms, start_id, count=count
            )

            next_id = result[0]
            if isinstance(next_id, bytes):
                next_id = next_id.decode("utf-8")

            messages = []
            for msg_data in result[1]:
                if len(msg_data) < 2:
                    continue

                msg_id = msg_data[0]
                if isinstance(msg_id, bytes):
                    msg_id = msg_id.decode("utf-8")

                data = self._decode_message_data(msg_data[1])

                messages.append(
                    StreamMessage(msg_id=msg_id, data=data, stream_key=stream_key)
                )

            deleted_ids = []
            if len(result) > 2:
                for del_id in result[2]:
                    if isinstance(del_id, bytes):
                        deleted_ids.append(del_id.decode("utf-8"))
                    else:
                        deleted_ids.append(del_id)

            return next_id, messages, deleted_ids

        except Exception as e:
            logger.error(f"XAUTOCLAIM 失败: {stream_key}, 错误: {e}")
            return "0-0", [], []

    # =========================================================================
    # 队列信息查询
    # =========================================================================

    async def xlen(self, stream_key: str) -> int:
        """获取 Stream 长度"""
        client = await self._get_client()
        return await client.xlen(stream_key)

    async def xpending(self, stream_key: str, group_name: str | None = None) -> dict:
        """获取 pending 消息摘要"""
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            result = await client.xpending(stream_key, group)

            if not result or result[0] == 0:
                return {
                    "pending_count": 0,
                    "min_id": None,
                    "max_id": None,
                    "consumers": {},
                }

            consumers = {}
            if result[3]:
                for consumer_data in result[3]:
                    name = consumer_data[0]
                    if isinstance(name, bytes):
                        name = name.decode("utf-8")
                    count = (
                        int(consumer_data[1])
                        if isinstance(consumer_data[1], bytes)
                        else consumer_data[1]
                    )
                    consumers[name] = count

            min_id = result[1]
            max_id = result[2]
            if isinstance(min_id, bytes):
                min_id = min_id.decode("utf-8")
            if isinstance(max_id, bytes):
                max_id = max_id.decode("utf-8")

            return {
                "pending_count": result[0],
                "min_id": min_id,
                "max_id": max_id,
                "consumers": consumers,
            }

        except Exception as e:
            if "NOGROUP" in str(e):
                return {
                    "pending_count": 0,
                    "min_id": None,
                    "max_id": None,
                    "consumers": {},
                }
            raise

    # =========================================================================
    # 清理操作
    # =========================================================================

    async def xtrim(self, stream_key: str, maxlen: int, approximate: bool = True) -> int:
        """裁剪 Stream"""
        client = await self._get_client()
        return await client.xtrim(stream_key, maxlen=maxlen, approximate=approximate)

    async def delete_stream(self, stream_key: str) -> bool:
        """删除整个 Stream"""
        client = await self._get_client()
        result = await client.delete(stream_key)
        return bool(result)
