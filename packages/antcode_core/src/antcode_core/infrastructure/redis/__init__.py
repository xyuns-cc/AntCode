"""
Redis 模块

Redis 客户端与工具：
- client: Redis 连接池管理
- keys: Key 命名规范
- streams: Redis Streams 封装（XADD/XREADGROUP/XACK/XAUTOCLAIM）
- locks: 分布式锁（compare-and-renew + fencing token）
"""

from antcode_core.infrastructure.redis.client import (
    RedisConnectionPool,
    close_redis_pool,
    get_redis_client,
)
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_core.infrastructure.redis.locks import (
    DistributedLock,
    FencingTokenManager,
    acquire_leader_lock,
    fencing_token_manager,
)
from antcode_core.infrastructure.redis.streams import StreamClient

__all__ = [
    "RedisConnectionPool",
    "get_redis_client",
    "close_redis_pool",
    "RedisKeys",
    "StreamClient",
    "DistributedLock",
    "FencingTokenManager",
    "fencing_token_manager",
    "acquire_leader_lock",
]
