"""StreamClient 的连接与 codec 持有层

各操作 Mixin（publish / group / read / ack / admin）都继承本类，共享
同一个 Redis 客户端与 codec，避免把连接管理复制到每个 Mixin。
"""

from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.stream_codec import JsonCodec, StreamCodec


class StreamSession:
    """持有 Redis 客户端与 codec 的基类

    Args:
        redis_client: Redis 客户端实例，为 None 时从连接池获取
        codec: Stream 编解码策略，None 时 typed 方法使用 ``JsonCodec``
    """

    def __init__(self, redis_client=None, codec: StreamCodec | None = None):
        self._redis = redis_client
        self._codec: StreamCodec = codec if codec is not None else JsonCodec()

    async def _get_client(self):
        """获取 Redis 客户端（连接层的重连由 redis-py 连接池负责）"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    @property
    def codec(self) -> StreamCodec:
        """当前注入的 codec（默认为 ``JsonCodec``）"""
        return self._codec


__all__ = ["StreamSession"]
