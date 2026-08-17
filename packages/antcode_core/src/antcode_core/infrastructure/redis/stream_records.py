"""Stream 消息数据结构与原始响应解析

从 ``stream_client`` 拆出的纯函数层：不接触 Redis 连接，只负责把
redis-py 返回的原始结构（bytes / 元组 / dict）转换成领域数据类。
"""

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from loguru import logger

from antcode_core.common.serialization import try_from_json
from antcode_core.infrastructure.redis.stream_codec import StreamCodec

T = TypeVar("T")

# redis-py 把 stream 响应表达为 2 元组：
#   XREADGROUP -> (stream_name, entries)
#   entry      -> (msg_id, fields)
# 短于该长度说明响应结构不符合预期，直接跳过而不是索引越界。
STREAM_TUPLE_ARITY = 2

# XAUTOCLAIM 响应为 [next_start_id, entries, deleted_ids]，第三段在
# Redis < 7.0 上不存在。
XAUTOCLAIM_DELETED_INDEX = 2


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


def decode_text(value: Any) -> str:
    """把 redis-py 返回的 bytes / 其他标量统一成 str。"""
    if value is None:
        return ""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def decode_message_data(raw_data: Any) -> dict:
    """解码消息字段 dict（每字段独立探测 JSON，非 JSON 字段保留原字符串）。

    Stream 字段天然双模：结构化字段写成 JSON，标量字段原样写入
    （``worker-001`` / ISO 时间戳 / 空串 / ``True``）。这里必须用探测式的
    ``try_from_json``——早先直接调 ``from_json`` 让这条被文档化的预期分支每次
    任务下发都打 2~4 条 ERROR，噪声随吞吐线性增长并淹没真实反序列化故障。
    """
    data: dict = {}
    if not isinstance(raw_data, dict):
        return data
    for k, v in raw_data.items():
        value = v.decode("utf-8") if isinstance(v, bytes) else v
        matched, parsed = try_from_json(value)
        data[decode_text(k)] = parsed if matched else value
    return data


def _iter_stream_entries(result: Any):
    """遍历 XREAD/XREADGROUP 结果，产出 (stream_name, entries)。"""
    for stream_data in result or []:
        if len(stream_data) < STREAM_TUPLE_ARITY:
            continue
        yield decode_text(stream_data[0]), stream_data[1]


def parse_entries(entries: Any, stream_name: str) -> list[StreamMessage]:
    """把单个 stream 的 entries 解析为 ``StreamMessage`` 列表。"""
    messages: list[StreamMessage] = []
    for msg_data in entries or []:
        if len(msg_data) < STREAM_TUPLE_ARITY:
            continue
        messages.append(
            StreamMessage(
                msg_id=decode_text(msg_data[0]),
                data=decode_message_data(msg_data[1]),
                stream_key=stream_name,
            )
        )
    return messages


def parse_xread_result(result: Any) -> list[StreamMessage]:
    """解析单 Stream 的 XREAD/XREADGROUP 结果。"""
    messages: list[StreamMessage] = []
    for stream_name, entries in _iter_stream_entries(result):
        messages.extend(parse_entries(entries, stream_name))
    return messages


def parse_xread_result_multi(result: Any) -> dict[str, list[StreamMessage]]:
    """解析多 Stream 读取结果，按 stream 键分组。"""
    return {stream_name: parse_entries(entries, stream_name) for stream_name, entries in _iter_stream_entries(result)}


def decode_typed_entries(entries: Any, stream_name: str, codec: StreamCodec) -> list[TypedStreamMessage]:
    """对单个 stream 的 entries 列表执行 codec.decode

    P1-19: 解码失败时不再静默跳过——那样上层看不到消息、也不会 ACK /
    不会 DLQ，坏消息会永久卡 PEL。改为返回一个"raw envelope"
    （``payload=None`` + ``decode_error`` + ``raw_fields``），让调用方能
    识别并转投死信队列。
    """
    out: list[TypedStreamMessage] = []
    for msg_data in entries or []:
        if len(msg_data) < STREAM_TUPLE_ARITY:
            continue
        msg_id = decode_text(msg_data[0])
        raw_fields = msg_data[1]
        try:
            payload = codec.decode(raw_fields)
        except Exception as exc:  # noqa: BLE001 - 坏消息必须交给上层转 DLQ
            logger.error(
                "Codec 解码失败(将由上层转 DLQ): stream={}, msg_id={}, codec={}, err={}",
                stream_name,
                msg_id,
                type(codec).__name__,
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
        out.append(TypedStreamMessage(msg_id=msg_id, payload=payload, stream_key=stream_name))
    return out


def parse_typed_result(result: Any, codec: StreamCodec) -> list[TypedStreamMessage]:
    """解析单 Stream 的 XREADGROUP 结果并通过 codec 解码 payload。"""
    messages: list[TypedStreamMessage] = []
    for stream_name, entries in _iter_stream_entries(result):
        messages.extend(decode_typed_entries(entries, stream_name, codec))
    return messages


def parse_typed_result_multi(result: Any, codec: StreamCodec) -> dict[str, list[TypedStreamMessage]]:
    """解析多 Stream 的 typed 读取结果。"""
    return {
        stream_name: decode_typed_entries(entries, stream_name, codec)
        for stream_name, entries in _iter_stream_entries(result)
    }


def parse_pending_message(item: Any) -> PendingMessage:
    """解析 XPENDING 明细项（兼容 dict 与元组两种 redis-py 响应形态）。"""
    if isinstance(item, dict):
        values = (
            item.get("message_id"),
            item.get("consumer"),
            item.get("time_since_delivered", 0),
            item.get("times_delivered", 0),
        )
    else:
        values = item
    return PendingMessage(
        msg_id=decode_text(values[0]),
        consumer=decode_text(values[1]),
        idle_time_ms=int(values[2]),
        delivery_count=int(values[3]),
    )


__all__ = [
    "STREAM_TUPLE_ARITY",
    "XAUTOCLAIM_DELETED_INDEX",
    "PendingMessage",
    "StreamMessage",
    "TypedStreamMessage",
    "decode_message_data",
    "decode_text",
    "decode_typed_entries",
    "parse_entries",
    "parse_pending_message",
    "parse_typed_result",
    "parse_typed_result_multi",
    "parse_xread_result",
    "parse_xread_result_multi",
]
