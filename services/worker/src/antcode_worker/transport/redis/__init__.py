"""
Redis Transport 模块

提供 Direct 模式下 Worker 与 Redis 的通信实现。

包含：
- keys: Redis key 命名规范
- transport: Redis 传输层实现
- codecs: 消息编解码
- reclaim: Pending 任务回收

Requirements: 5.3, 5.4
"""

from antcode_worker.transport.redis.codecs import (
    CodecError,
    ControlMessageCodec,
    HeartbeatCodec,
    JsonCodec,
    LogMessageCodec,
    MessageCodec,
    ResultMessageCodec,
    SchemaVersion,
    TaskMessageCodec,
    control_codec,
    default_codec,
    heartbeat_codec,
    log_codec,
    result_codec,
    task_codec,
)
from antcode_worker.transport.redis.keys import RedisKeyConfig, RedisKeys, default_keys
from antcode_worker.transport.redis.reclaim import (
    GlobalReclaimer,
    PendingTaskReclaimer,
    ReclaimConfig,
    ReclaimedTask,
    ReclaimStats,
    cleanup_dead_consumers,
    ensure_consumer_group,
)
from antcode_worker.transport.redis.transport import RedisTransport

__all__ = [
    # Transport
    "RedisTransport",
    # Keys
    "RedisKeys",
    "RedisKeyConfig",
    "default_keys",
    # Codecs
    "MessageCodec",
    "JsonCodec",
    "TaskMessageCodec",
    "LogMessageCodec",
    "ResultMessageCodec",
    "HeartbeatCodec",
    "ControlMessageCodec",
    "SchemaVersion",
    "CodecError",
    # Default codec instances
    "default_codec",
    "task_codec",
    "log_codec",
    "result_codec",
    "heartbeat_codec",
    "control_codec",
    # Reclaim
    "PendingTaskReclaimer",
    "GlobalReclaimer",
    "ReclaimedTask",
    "ReclaimConfig",
    "ReclaimStats",
    "ensure_consumer_group",
    "cleanup_dead_consumers",
]
