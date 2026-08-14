"""Redis Streams 客户端（唯一规范实现）

提供 Redis Streams 的高级操作封装，支持：
- 消息发布 (XADD)
- 消费者组管理 (XGROUP)
- 消息读取 (XREADGROUP)
- 消息确认 (XACK)
- 超时任务转移 (XCLAIM/XAUTOCLAIM)
- 队列信息查询 (XLEN/XINFO/XPENDING)

支持可插拔的消息编解码策略（Codec）：
- JsonCodec（默认）：每个业务字段独立 JSON 序列化
- ProtoCodec：将 Proto Message 序列化为单字段 'p' 存原始 bytes

**B9 修复**：本模块曾与 ``streams.py`` 并存两份同名 ``StreamClient``，
两者的类级默认消费组名不同（``crawl_workers`` / ``default_workers``）。
调用方漏传 ``group_name`` 时会 ACK 到一个不存在的消费组，Redis 返回 0
而代码不校验，消息永久滞留 PEL 被 XAUTOCLAIM 反复重投。修复方式：
1. 只保留本实现，删除默认组名；
2. 所有消费组相关方法的 ``group_name`` 改为必填参数；
3. ``xack`` 校验返回值，0 条确认直接抛 ``StreamAckError``。

实现按职责拆分到同目录的 ``stream_*_ops`` 模块，本文件只负责组装。
"""

from antcode_core.infrastructure.redis.stream_ack_ops import StreamAckError, StreamAckMixin
from antcode_core.infrastructure.redis.stream_admin_ops import StreamAdminMixin
from antcode_core.infrastructure.redis.stream_codec import (
    PROTO_FIELD,
    JsonCodec,
    ProtoCodec,
    ProtoMessage,
    StreamCodec,
)
from antcode_core.infrastructure.redis.stream_group_ops import StreamGroupMixin
from antcode_core.infrastructure.redis.stream_publish_ops import StreamPublishMixin
from antcode_core.infrastructure.redis.stream_read_ops import StreamReadMixin
from antcode_core.infrastructure.redis.stream_records import PendingMessage, StreamMessage, TypedStreamMessage


class StreamClient(
    StreamPublishMixin,
    StreamReadMixin,
    StreamGroupMixin,
    StreamAckMixin,
    StreamAdminMixin,
):
    """Redis Streams 客户端

    封装 Redis Streams 的常用操作，提供：
    - 消息发布和读取
    - 消费者组管理
    - 超时任务回收
    - 队列状态查询

    可选注入 ``codec``：
    - ``codec=None`` (默认)：typed 方法使用 ``JsonCodec``。
    - ``codec=ProtoCodec(SomeProtoMsg)``：Proto bytes 编解码。

    Typed 方法 (``xadd_typed`` / ``xreadgroup_typed`` ...) 使用注入的 codec；
    无后缀方法保持 dict 接口。

    所有涉及消费者组的方法都要求显式传入 ``group_name``，没有默认组名。
    """


__all__ = [
    "PROTO_FIELD",
    "JsonCodec",
    "PendingMessage",
    "ProtoCodec",
    "ProtoMessage",
    "StreamAckError",
    "StreamClient",
    "StreamCodec",
    "StreamMessage",
    "TypedStreamMessage",
]
