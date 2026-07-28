"""Cross-process task log sequence allocation."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, cast

from antcode_core.application.services.logs.postgres_log_service import postgres_task_log_service
from antcode_core.infrastructure.redis import get_redis_client, redis_namespace

SEQUENCE_KEY_TTL_SECONDS = 8 * 24 * 60 * 60

# 键不存在时的哨兵返回值。合法分配结果恒为正（floor >= 0 且 count >= 1），
# 不会与 -1 冲突。
_MISSING_KEY_SENTINEL = -1

# 热路径：键已存在时直接 INCRBY，不回源 PG。键缺失返回哨兵，由调用方
# 查 PG 高水位后走播种脚本重试一次。
_ALLOCATE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return -1
end
local last = redis.call('INCRBY', KEYS[1], tonumber(ARGV[1]))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return last
"""

# 播种路径：服务端条件播种 —— 取 max(floor, current) 后再 INCRBY，整段
# 原子执行。两个并发播种者不会双重分配，崩溃恢复（键被淘汰）时也不会
# 把序号回拨到 PG 高水位以下。
_SEED_ALLOCATE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '-1')
local floor = tonumber(ARGV[1])
if current < floor then
    redis.call('SET', KEYS[1], floor)
end
local last = redis.call('INCRBY', KEYS[1], tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return last
"""


class LogSequenceAllocator(Protocol):
    async def allocate(self, run_id: str, log_type: str, count: int) -> list[int]:
        """Atomically allocate consecutive positive sequence numbers."""


class RedisLogSequenceAllocator:
    async def allocate(self, run_id: str, log_type: str, count: int) -> list[int]:
        if count <= 0:
            return []
        redis = await get_redis_client()
        if redis is None:
            raise RuntimeError("Redis 客户端不可用，无法分配日志序号")
        key = f"{redis_namespace()}:log:sequence:{run_id}:{log_type}"
        last = int(
            await cast(
                Awaitable[Any],
                redis.eval(_ALLOCATE_LUA, 1, key, count, SEQUENCE_KEY_TTL_SECONDS),
            )
        )
        if last == _MISSING_KEY_SENTINEL:
            # 仅在键缺失（首次分配 / TTL 过期）时回源 PG 查高水位，避免
            # 每次分配都执行一条 MAX 查询。播种脚本内部仍取
            # max(floor, current)，与哨兵检查之间的并发播种不会双重分配。
            floor = await postgres_task_log_service.max_sequence(run_id, log_type)
            last = int(
                await cast(
                    Awaitable[Any],
                    redis.eval(_SEED_ALLOCATE_LUA, 1, key, floor, count, SEQUENCE_KEY_TTL_SECONDS),
                )
            )
        return list(range(last - count + 1, last + 1))


redis_log_sequence_allocator = RedisLogSequenceAllocator()

__all__ = [
    "LogSequenceAllocator",
    "RedisLogSequenceAllocator",
    "redis_log_sequence_allocator",
]
