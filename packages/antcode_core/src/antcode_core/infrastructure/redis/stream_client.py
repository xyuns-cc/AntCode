"""Redis Streams 客户端封装

提供 Redis Streams 的高级操作封装，支持：
- 消息发布 (XADD)
- 消费者组管理 (XGROUP)
- 消息读取 (XREADGROUP)
- 消息确认 (XACK)
- 超时任务转移 (XCLAIM/XAUTOCLAIM)
- 队列信息查询 (XLEN/XINFO/XPENDING)

支持可插拔的消息编解码策略（Codec）：
- JsonCodec（默认，向后兼容）：每个业务字段独立 JSON 序列化
- ProtoCodec：将 Proto Message 序列化为单字段 'p' 存原始 bytes
"""

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from loguru import logger

from antcode_core.common.utils.serialization import from_json, to_json
from antcode_core.infrastructure.redis.client import get_redis_client

# Stream codec field name used by ProtoCodec to store serialized bytes
# (约定俗成：Proto 序列化字节统一存到 'p' 字段，便于 worker/gateway 协同)
PROTO_FIELD = b"p"

T = TypeVar("T")


class ProtoMessage(Protocol):
    """Structural subset of protobuf Message used by the stream codec."""

    def SerializeToString(self) -> bytes: ...

    def ParseFromString(self, data: bytes) -> int: ...


P = TypeVar("P", bound=ProtoMessage)


@dataclass
class StreamMessage:
    """Stream 消息数据类"""

    msg_id: str = ""
    data: dict = field(default_factory=dict)
    stream_key: str = ""


@dataclass
class TypedStreamMessage(Generic[T]):
    """带类型解码后的 Stream 消息

    `payload` 字段已经被对应的 codec 解码为业务对象（如 Proto Message）。

    P1-19 修复：当 codec.decode 抛异常时不再静默丢消息，而是把
    ``payload`` 置为 None 并填充 ``decode_error`` / ``raw_fields``。
    上层调用方检查 ``decode_error`` 就直接走死信路径，避免坏消息
    永久卡在 PEL、也避免被"当作没这条消息"漏 ACK。
    """

    msg_id: str = ""
    payload: Any = None
    stream_key: str = ""
    decode_error: str | None = None
    raw_fields: dict | None = None


@dataclass
class PendingMessage:
    """待处理消息信息"""

    msg_id: str = ""
    consumer: str = ""
    idle_time_ms: int = 0
    delivery_count: int = 0


class StreamCodec(Protocol, Generic[T]):
    """Stream 消息编解码策略

    Stream Codec 在 ``StreamClient`` 的 ``xadd_typed`` / ``xreadgroup_typed`` 等
    typed 方法上生效。它接收业务对象（如 Proto Message、dict 等）并产出
    Redis Stream 字段字典，反之亦然。

    实现需保证 ``decode(encode(msg))`` 的语义等价。
    """

    def encode(self, msg: T) -> dict[bytes | str, bytes | str]:
        """编码业务对象 → Redis Stream 字段 dict"""
        ...

    def decode(self, fields: dict[bytes | str, bytes | str]) -> T:
        """解码 Redis Stream 字段 dict → 业务对象"""
        ...


class JsonCodec:
    """JSON 序列化（向后兼容）

    与 ``StreamClient`` 的传统行为等价：每个字段独立 JSON 序列化。
    string/int/float/bytes 直接透传，其他类型用 ``to_json``。
    解码时尝试 JSON 反序列化，失败则保留原值。
    """

    def encode(self, msg: dict) -> dict:
        if not isinstance(msg, dict):
            raise TypeError(f"JsonCodec.encode expects dict, got {type(msg).__name__}")
        return {k: self._encode_value(v) for k, v in msg.items()}

    @staticmethod
    def _encode_value(value):
        if isinstance(value, bool):
            return to_json(value)
        if isinstance(value, (str, int, float, bytes)):
            return value
        return to_json(value)

    def decode(self, fields: dict) -> dict:
        out: dict = {}
        for k, v in fields.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            if isinstance(v, bytes):
                try:
                    v = v.decode("utf-8")
                except UnicodeDecodeError:
                    out[key] = v
                    continue
            if isinstance(v, str):
                # 尝试 JSON 反序列化；任何反序列化错误回退到原字符串
                # (使用宽 try 是因为 ``from_json`` 可能抛 SerializationError /
                # JSONDecodeError / ValueError / TypeError 等多种异常)
                try:
                    out[key] = from_json(v)
                    continue
                except Exception:  # noqa: BLE001 - 反序列化回退是预期路径
                    pass
            out[key] = v
        return out


class ProtoCodec(Generic[P]):
    """Proto bytes 序列化

    将 Proto Message 整个序列化为字节并存到单字段（默认 'p'）。读取时
    把字节反序列化回 Proto Message。

    Args:
        msg_type: Proto Message 类（如 ``data_pb2.TaskStatus``）。
        field_name: 存放序列化字节的字段名，默认 ``b"p"``。
    """

    def __init__(self, msg_type: type[P], field_name: bytes = PROTO_FIELD):
        self._msg_type = msg_type
        self._field = field_name
        self._field_str = field_name.decode("utf-8") if isinstance(field_name, bytes) else field_name

    @property
    def msg_type(self) -> type[P]:
        return self._msg_type

    def encode(self, msg: P) -> dict:
        if not hasattr(msg, "SerializeToString"):
            raise TypeError(f"ProtoCodec.encode expects a Proto Message, got {type(msg).__name__}")
        return {self._field: msg.SerializeToString()}

    def decode(self, fields: dict) -> P:
        raw = fields.get(self._field)
        if raw is None:
            raw = fields.get(self._field_str)
        if raw is None:
            raise ValueError(f"missing '{self._field_str}' field for proto codec ({self._msg_type.__name__})")
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        msg = self._msg_type()
        msg.ParseFromString(raw)
        return msg


class StreamClient:
    """Redis Streams 客户端

    封装 Redis Streams 的常用操作，提供：
    - 消息发布和读取
    - 消费者组管理
    - 超时任务回收
    - 队列状态查询

    可选注入 ``codec``：
    - ``codec=None`` (默认)：保持历史 JSON 行为（隐式 ``JsonCodec``）。
    - ``codec=JsonCodec()``：显式 JSON 编解码。
    - ``codec=ProtoCodec(SomeProtoMsg)``：Proto bytes 编解码。

    Typed 方法 (``xadd_typed`` / ``xreadgroup_typed`` ...) 使用注入的 codec；
    无后缀方法保持原 dict 接口，不破坏历史调用方。
    """

    # 默认消费者组名称
    DEFAULT_GROUP = "crawl_workers"

    def __init__(
        self,
        redis_client=None,
        codec: StreamCodec | None = None,
    ):
        """初始化 Stream 客户端

        Args:
            redis_client: Redis 客户端实例，为 None 时自动获取
            codec: Stream 编解码策略，None 时 typed 方法使用 ``JsonCodec``
        """
        self._redis = redis_client
        self._codec: StreamCodec = codec if codec is not None else JsonCodec()

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    @property
    def codec(self) -> StreamCodec:
        """当前注入的 codec（默认为 ``JsonCodec``）"""
        return self._codec

    # =========================================================================
    # 消息发布
    # =========================================================================

    async def xadd(
        self, stream_key: str, data: dict, msg_id: str = "*", maxlen: int | None = None, approximate: bool = True
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
        serialized = {k: to_json(v) if not isinstance(v, (str, bytes)) else v for k, v in data.items()}

        kwargs = {}
        if maxlen is not None:
            kwargs["maxlen"] = maxlen
            kwargs["approximate"] = approximate

        result = await client.xadd(stream_key, serialized, id=msg_id, **kwargs)

        # 结果可能是 bytes 或 str
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return result

    async def xadd_batch(self, stream_key: str, messages: list, maxlen: int | None = None) -> list:
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
            serialized = {k: to_json(v) if not isinstance(v, (str, bytes)) else v for k, v in data.items()}
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

    async def xadd_typed(
        self,
        stream_key: str,
        msg: Any,
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

        kwargs = {}
        if maxlen is not None:
            kwargs["maxlen"] = maxlen
            kwargs["approximate"] = approximate

        result = await client.xadd(stream_key, serialized, id=msg_id, **kwargs)
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return result

    # =========================================================================
    # 消费者组管理
    # =========================================================================

    async def xgroup_create(
        self, stream_key: str, group_name: str | None = None, start_id: str = "0", mkstream: bool = True
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
            # 组已存在不算错误
            if "BUSYGROUP" in str(e):
                logger.debug(f"消费者组已存在: {stream_key} -> {group}")
                return True
            logger.error(f"创建消费者组失败: {stream_key} -> {group}, 错误: {e}")
            raise

    async def xgroup_destroy(self, stream_key: str, group_name: str | None = None) -> bool:
        """删除消费者组

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称

        Returns:
            是否删除成功
        """
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
        """确保消费者组存在

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称

        Returns:
            是否成功
        """
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
    ) -> list:
        """从消费者组读取消息

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称
            consumer_name: 消费者名称
            count: 读取数量
            block_ms: 阻塞等待毫秒数，None 表示不阻塞
            read_pending: 是否读取 pending 消息（使用 "0" 而非 ">"）

        Returns:
            StreamMessage 列表
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        # ">" 读取新消息，"0" 读取 pending 消息
        msg_id = "0" if read_pending else ">"

        kwargs = {"count": count}
        if block_ms is not None:
            kwargs["block"] = block_ms

        try:
            result = await client.xreadgroup(group, consumer_name, streams={stream_key: msg_id}, **kwargs)

            return self._parse_xread_result(result, stream_key)

        except Exception as e:
            # 组不存在时自动创建
            if "NOGROUP" in str(e):
                await self.ensure_group(stream_key, group)
                return []
            raise

    async def xreadgroup_multi(
        self,
        stream_keys: list,
        group_name: str | None = None,
        consumer_name: str = "worker",
        count: int = 10,
        block_ms: int | None = None,
    ) -> dict:
        """从多个 Stream 读取消息（按优先级）

        Args:
            stream_keys: Stream 键名列表（按优先级排序）
            group_name: 消费者组名称
            consumer_name: 消费者名称
            count: 每个 Stream 读取数量
            block_ms: 阻塞等待毫秒数

        Returns:
            {stream_key: [StreamMessage]} 字典
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        # 确保所有 Stream 的消费者组存在
        for key in stream_keys:
            await self.ensure_group(key, group)

        streams = dict.fromkeys(stream_keys, ">")

        kwargs = {"count": count}
        if block_ms is not None:
            kwargs["block"] = block_ms

        try:
            result = await client.xreadgroup(group, consumer_name, streams=streams, **kwargs)

            return self._parse_xread_result_multi(result)

        except Exception as e:
            logger.error(f"多 Stream 读取失败: {e}")
            raise

    async def xreadgroup_typed(
        self,
        stream_key: str,
        group_name: str | None = None,
        consumer_name: str = "worker",
        count: int = 10,
        block_ms: int | None = None,
        read_pending: bool = False,
    ) -> list[TypedStreamMessage]:
        """使用注入的 codec 解码后读取消息

        与 ``xreadgroup`` 接口对齐，但每条消息的 ``payload`` 已经被
        ``codec.decode`` 解码为业务对象。codec 解码失败的消息会被
        跳过（记录错误日志）以避免阻塞整批。

        Returns:
            TypedStreamMessage 列表，``payload`` 字段为 codec 解码后的对象
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        msg_id = "0" if read_pending else ">"

        kwargs = {"count": count}
        if block_ms is not None:
            kwargs["block"] = block_ms

        try:
            result = await client.xreadgroup(group, consumer_name, streams={stream_key: msg_id}, **kwargs)
            return self._parse_typed_result(result, stream_key)

        except Exception as e:
            if "NOGROUP" in str(e):
                await self.ensure_group(stream_key, group)
                return []
            raise

    async def xreadgroup_multi_typed(
        self,
        stream_keys: list,
        group_name: str | None = None,
        consumer_name: str = "worker",
        count: int = 10,
        block_ms: int | None = None,
    ) -> dict[str, list[TypedStreamMessage]]:
        """多 Stream 版本的 ``xreadgroup_typed``。"""
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        for key in stream_keys:
            await self.ensure_group(key, group)

        streams = dict.fromkeys(stream_keys, ">")

        kwargs = {"count": count}
        if block_ms is not None:
            kwargs["block"] = block_ms

        try:
            result = await client.xreadgroup(group, consumer_name, streams=streams, **kwargs)

            result_dict: dict[str, list[TypedStreamMessage]] = {}
            if not result:
                return result_dict
            for stream_data in result:
                if len(stream_data) < 2:
                    continue
                stream_name = stream_data[0]
                if isinstance(stream_name, bytes):
                    stream_name = stream_name.decode("utf-8")
                result_dict[stream_name] = self._decode_typed_entries(stream_data[1], stream_name)
            return result_dict

        except Exception as e:
            logger.error(f"多 Stream typed 读取失败: {e}")
            raise

    def _parse_typed_result(self, result, stream_key: str) -> list[TypedStreamMessage]:
        """解析 XREAD/XREADGROUP 结果并通过 codec 解码 payload"""
        if not result:
            return []

        messages: list[TypedStreamMessage] = []
        for stream_data in result:
            if len(stream_data) < 2:
                continue
            stream_name = stream_data[0]
            if isinstance(stream_name, bytes):
                stream_name = stream_name.decode("utf-8")
            messages.extend(self._decode_typed_entries(stream_data[1], stream_name))
        return messages

    def _decode_typed_entries(self, entries, stream_name: str) -> list[TypedStreamMessage]:
        """对单个 stream 的 entries 列表执行 codec.decode

        P1-19: 解码失败时不再 ``continue`` 静默跳过——那样上层看不到消息、
        也不会 ACK / 不会 DLQ，坏消息会永久卡 PEL。改为返回一个"raw
        envelope"（``payload=None`` + ``decode_error`` + ``raw_fields``），
        让调用方能识别并转投死信队列。
        """
        out: list[TypedStreamMessage] = []
        for msg_data in entries:
            if len(msg_data) < 2:
                continue
            msg_id = msg_data[0]
            if isinstance(msg_id, bytes):
                msg_id = msg_id.decode("utf-8")
            raw_fields = msg_data[1]
            try:
                payload = self._codec.decode(raw_fields)
            except Exception as exc:
                logger.error(
                    "Codec 解码失败(将由上层转 DLQ): stream={}, msg_id={}, codec={}, err={}",
                    stream_name,
                    msg_id,
                    type(self._codec).__name__,
                    exc,
                )
                out.append(
                    TypedStreamMessage(
                        msg_id=msg_id,
                        payload=None,
                        stream_key=stream_name,
                        decode_error=f"{type(exc).__name__}: {exc}",
                        # raw_fields 保留原始 dict（bytes-key/bytes-value）方便
                        # 死信队列存档，注意里面可能含 Proto bytes，不做 utf-8 解码
                        raw_fields=dict(raw_fields) if isinstance(raw_fields, dict) else None,
                    )
                )
                continue
            out.append(
                TypedStreamMessage(
                    msg_id=msg_id,
                    payload=payload,
                    stream_key=stream_name,
                )
            )
        return out

    def _parse_xread_result(self, result, stream_key: str) -> list:
        """解析 XREAD/XREADGROUP 结果"""
        messages: list[StreamMessage] = []

        if not result:
            return messages

        for stream_data in result:
            # stream_data 示例: [stream_name, [(msg_id, {field: value}), ...]]
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

                # 解析消息数据
                data = self._decode_message_data(msg_data[1])

                messages.append(StreamMessage(msg_id=msg_id, data=data, stream_key=stream_name))

        return messages

    def _parse_xread_result_multi(self, result) -> dict:
        """解析多 Stream 读取结果"""
        result_dict: dict[str, list[StreamMessage]] = {}

        if not result:
            return result_dict

        for stream_data in result:
            if len(stream_data) < 2:
                continue

            stream_name = stream_data[0]
            if isinstance(stream_name, bytes):
                stream_name = stream_name.decode("utf-8")

            messages = []
            for msg_data in stream_data[1]:
                if len(msg_data) < 2:
                    continue

                msg_id = msg_data[0]
                if isinstance(msg_id, bytes):
                    msg_id = msg_id.decode("utf-8")

                data = self._decode_message_data(msg_data[1])

                messages.append(StreamMessage(msg_id=msg_id, data=data, stream_key=stream_name))

            result_dict[stream_name] = messages

        return result_dict

    def _decode_message_data(self, raw_data) -> dict:
        """解码消息数据"""
        data = {}

        if isinstance(raw_data, dict):
            for k, v in raw_data.items():
                key = k.decode("utf-8") if isinstance(k, bytes) else k
                value = v.decode("utf-8") if isinstance(v, bytes) else v

                # 尝试 JSON 反序列化
                try:
                    data[key] = from_json(value)
                except Exception:
                    data[key] = value

        return data

    # =========================================================================
    # 消息确认
    # =========================================================================

    async def xack(self, stream_key: str, msg_ids: list, group_name: str | None = None) -> int:
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

        result = await client.xack(stream_key, group, *msg_ids)
        return result

    async def xdel(self, stream_key: str, msg_ids: list) -> int:
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
        result = await client.xdel(stream_key, *msg_ids)
        return result

    # =========================================================================
    # 超时任务转移
    # =========================================================================

    async def xclaim(
        self,
        stream_key: str,
        msg_ids: list,
        group_name: str | None = None,
        consumer_name: str = "worker",
        min_idle_time_ms: int = 0,
        retry_count: int | None = None,
    ) -> list:
        """转移消息所有权

        Args:
            stream_key: Stream 键名
            msg_ids: 消息 ID 列表
            group_name: 消费者组名称
            consumer_name: 新的消费者名称
            min_idle_time_ms: 最小空闲时间（毫秒）
            retry_count: 设置重试计数，None 表示不修改

        Returns:
            成功转移的 StreamMessage 列表
        """
        if not msg_ids:
            return []

        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        kwargs = {}
        if retry_count is not None:
            kwargs["retrycount"] = retry_count

        try:
            result = await client.xclaim(stream_key, group, consumer_name, min_idle_time_ms, msg_ids, **kwargs)

            messages = []
            for msg_data in result:
                if len(msg_data) < 2:
                    continue

                msg_id = msg_data[0]
                if isinstance(msg_id, bytes):
                    msg_id = msg_id.decode("utf-8")

                data = self._decode_message_data(msg_data[1])

                messages.append(StreamMessage(msg_id=msg_id, data=data, stream_key=stream_key))

            return messages

        except Exception as e:
            logger.error(f"XCLAIM 失败: {stream_key}, 错误: {e}")
            raise

    async def xautoclaim(
        self,
        stream_key: str,
        group_name: str | None = None,
        consumer_name: str = "worker",
        min_idle_time_ms: int = 300000,
        start_id: str = "0-0",
        count: int = 100,
    ) -> tuple:
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
            result = await client.xautoclaim(stream_key, group, consumer_name, min_idle_time_ms, start_id, count=count)

            # result 示例: [next_start_id, [(msg_id, {data}), ...], [deleted_ids]]
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

                messages.append(StreamMessage(msg_id=msg_id, data=data, stream_key=stream_key))

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
            raise

    # =========================================================================
    # 队列信息查询
    # =========================================================================

    async def xlen(self, stream_key: str) -> int:
        """获取 Stream 长度

        Args:
            stream_key: Stream 键名

        Returns:
            消息数量
        """
        client = await self._get_client()
        return await client.xlen(stream_key)

    async def xpending(self, stream_key: str, group_name: str | None = None) -> dict:
        """获取 pending 消息摘要

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称

        Returns:
            {pending_count, min_id, max_id, consumers: {name: count}}
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            result = await client.xpending(stream_key, group)

            if not result or result[0] == 0:
                return {"pending_count": 0, "min_id": None, "max_id": None, "consumers": {}}

            # result 示例: [count, min_id, max_id, [[consumer, count], ...]]
            consumers = {}
            if result[3]:
                for consumer_data in result[3]:
                    name = consumer_data[0]
                    if isinstance(name, bytes):
                        name = name.decode("utf-8")
                    count = int(consumer_data[1]) if isinstance(consumer_data[1], bytes) else consumer_data[1]
                    consumers[name] = count

            min_id = result[1]
            max_id = result[2]
            if isinstance(min_id, bytes):
                min_id = min_id.decode("utf-8")
            if isinstance(max_id, bytes):
                max_id = max_id.decode("utf-8")

            return {"pending_count": result[0], "min_id": min_id, "max_id": max_id, "consumers": consumers}

        except Exception as e:
            if "NOGROUP" in str(e):
                return {"pending_count": 0, "min_id": None, "max_id": None, "consumers": {}}
            raise

    async def xpending_range(
        self,
        stream_key: str,
        group_name: str | None = None,
        start: str = "-",
        end: str = "+",
        count: int = 100,
        consumer_name: str | None = None,
    ) -> list:
        """获取 pending 消息详情

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称
            start: 起始 ID
            end: 结束 ID
            count: 数量限制
            consumer_name: 指定消费者，None 表示所有

        Returns:
            PendingMessage 列表
        """
        client = await self._get_client()
        group = group_name or self.DEFAULT_GROUP

        try:
            kwargs = {}
            if consumer_name:
                kwargs["consumername"] = consumer_name

            result = await client.xpending_range(stream_key, group, start, end, count, **kwargs)

            messages = []
            for item in result:
                # item 示例: [msg_id, consumer, idle_time, delivery_count]
                msg_id = item[0]
                consumer = item[1]
                if isinstance(msg_id, bytes):
                    msg_id = msg_id.decode("utf-8")
                if isinstance(consumer, bytes):
                    consumer = consumer.decode("utf-8")

                messages.append(
                    PendingMessage(msg_id=msg_id, consumer=consumer, idle_time_ms=item[2], delivery_count=item[3])
                )

            return messages

        except Exception as e:
            if "NOGROUP" in str(e):
                return []
            raise

    async def xinfo_stream(self, stream_key: str) -> dict:
        """获取 Stream 信息

        Args:
            stream_key: Stream 键名

        Returns:
            Stream 信息字典
        """
        client = await self._get_client()

        try:
            result = await client.xinfo_stream(stream_key)

            # 解码 bytes
            info = {}
            for k, v in result.items():
                key = k.decode("utf-8") if isinstance(k, bytes) else k
                value = v.decode("utf-8") if isinstance(v, bytes) else v
                info[key] = value

            return info

        except Exception as e:
            if "no such key" in str(e).lower():
                return {}
            raise

    async def xinfo_groups(self, stream_key: str) -> list:
        """获取消费者组信息

        Args:
            stream_key: Stream 键名

        Returns:
            消费者组信息列表
        """
        client = await self._get_client()

        try:
            result = await client.xinfo_groups(stream_key)

            groups = []
            for group_data in result:
                group = {}
                for k, v in group_data.items():
                    key = k.decode("utf-8") if isinstance(k, bytes) else k
                    value = v.decode("utf-8") if isinstance(v, bytes) else v
                    group[key] = value
                groups.append(group)

            return groups

        except Exception as e:
            if "no such key" in str(e).lower():
                return []
            raise

    # =========================================================================
    # 清理操作
    # =========================================================================

    async def xtrim(
        self, stream_key: str, maxlen: int | None = None, approximate: bool = True, minid: str | None = None
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
            # redis-py 支持 xtrim(name, minid=..., approximate=...)。
            try:
                return await client.xtrim(stream_key, minid=minid, approximate=approximate)
            except TypeError as exc:
                raise RuntimeError("当前 redis-py 不支持安全的 XTRIM MINID") from exc
        if maxlen is None:
            raise ValueError("xtrim 需要 maxlen 或 minid 至少一个参数")
        return await client.xtrim(stream_key, maxlen=maxlen, approximate=approximate)

    async def delete_stream(self, stream_key: str) -> bool:
        """删除整个 Stream

        Args:
            stream_key: Stream 键名

        Returns:
            是否删除成功
        """
        client = await self._get_client()
        result = await client.delete(stream_key)
        return bool(result)
