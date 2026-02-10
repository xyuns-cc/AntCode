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
from antcode_core.infrastructure.redis.control_plane import (
    build_cancel_control_payload,
    build_config_update_control_payload,
    build_runtime_manage_control_payload,
    control_global_stream,
    control_group,
    control_reply_stream,
    control_stream,
    decode_stream_payload,
    direct_register_proof_key,
    redis_namespace,
    task_ready_stream,
    task_result_stream,
    log_stream_key,
    log_chunk_stream_key,
    log_stream_pattern,
    log_chunk_stream_pattern,
    worker_group,
    worker_heartbeat_key,
    worker_install_key_block_key,
    worker_install_key_claim_key,
    worker_install_key_fail_counter_key,
    worker_install_key_meta_key,
    worker_install_key_nonce_key,
)

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
    "redis_namespace",
    "task_ready_stream",
    "task_result_stream",
    "log_stream_key",
    "log_chunk_stream_key",
    "log_stream_pattern",
    "log_chunk_stream_pattern",
    "control_stream",
    "control_global_stream",
    "control_reply_stream",
    "worker_heartbeat_key",
    "worker_group",
    "control_group",
    "direct_register_proof_key",
    "worker_install_key_fail_counter_key",
    "worker_install_key_block_key",
    "worker_install_key_claim_key",
    "worker_install_key_nonce_key",
    "worker_install_key_meta_key",
    "build_cancel_control_payload",
    "build_config_update_control_payload",
    "build_runtime_manage_control_payload",
    "decode_stream_payload",
]
