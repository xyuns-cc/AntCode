"""Stream 消息编解码策略（Codec）

从 ``stream_client`` 拆出，职责单一：把业务对象与 Redis Stream 字段
字典互相转换。两种实现：

- ``JsonCodec``：每个业务字段独立 JSON 序列化（历史 dict 行为）。
- ``ProtoCodec``：整个 Proto Message 序列化为单字段 'p' 的原始 bytes。
"""

from typing import Generic, Protocol, TypeVar

from antcode_core.common.serialization import from_json, to_json

# Proto 序列化字节统一存到 'p' 字段，便于 worker/gateway 协同
PROTO_FIELD = b"p"

T = TypeVar("T")


class ProtoMessage(Protocol):
    """Structural subset of protobuf Message used by the stream codec."""

    def SerializeToString(self) -> bytes: ...

    def ParseFromString(self, data: bytes) -> int: ...


P = TypeVar("P", bound=ProtoMessage)


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
    """JSON 序列化

    与 ``StreamClient`` 无后缀方法的 dict 行为等价：每个字段独立 JSON
    序列化。string/int/float/bytes 直接透传，其他类型用 ``to_json``。
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


__all__ = [
    "PROTO_FIELD",
    "JsonCodec",
    "ProtoCodec",
    "ProtoMessage",
    "StreamCodec",
]
