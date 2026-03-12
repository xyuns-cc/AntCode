"""Redis 分布式滑动窗口限流器

使用 Sorted Set + Lua 脚本实现原子化滑动窗口限流。
Redis 不可用时 fail-open（放行请求）。
"""

from loguru import logger

# Lua 脚本：原子化滑动窗口限流
# KEYS[1] = 限流 key
# ARGV[1] = 窗口大小（秒）
# ARGV[2] = 最大请求数
# ARGV[3] = 当前时间戳（微秒）
# 返回: [当前请求数, 是否允许(1/0)]
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cutoff = now - window * 1000000

-- 移除过期记录
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)

-- 当前窗口请求数
local count = redis.call('ZCARD', key)

if count < limit then
    -- 添加当前请求
    redis.call('ZADD', key, now, now .. ':' .. math.random(1000000))
    -- 设置 key 过期时间为窗口大小的 2 倍
    redis.call('EXPIRE', key, window * 2)
    return {count + 1, 1}
else
    -- 仍然刷新过期时间
    redis.call('EXPIRE', key, window * 2)
    return {count, 0}
end
"""


class RedisRateLimiter:
    """Redis 分布式滑动窗口限流器"""

    def __init__(self, key_prefix: str = "ratelimit:"):
        self._key_prefix = key_prefix
        self._script_sha: str | None = None

    async def _ensure_script(self, redis_client) -> str:
        """加载 Lua 脚本并缓存 SHA"""
        if self._script_sha is None:
            self._script_sha = await redis_client.script_load(_SLIDING_WINDOW_SCRIPT)
        return self._script_sha

    async def is_allowed(
        self,
        identifier: str,
        limit: int,
        period: int,
    ) -> bool:
        """检查请求是否被允许

        Args:
            identifier: 限流标识（如客户端 IP）
            limit: 窗口内最大请求数
            period: 窗口大小（秒）

        Returns:
            True 表示允许，False 表示被限流
        """
        try:
            from antcode_core.infrastructure.redis.client import get_redis_client

            redis_client = await get_redis_client()
            key = f"{self._key_prefix}{identifier}"

            import time

            now_us = int(time.time() * 1_000_000)

            sha = await self._ensure_script(redis_client)
            try:
                result = await redis_client.evalsha(sha, 1, key, period, limit, now_us)
            except Exception:
                # SHA 可能因 Redis 重启失效，重新加载
                self._script_sha = None
                sha = await self._ensure_script(redis_client)
                result = await redis_client.evalsha(sha, 1, key, period, limit, now_us)

            # result = [count, allowed(1/0)]
            return result[1] == 1

        except Exception as e:
            # Redis 不可用时 fail-open
            logger.warning(f"Redis 限流检查失败，放行请求: {e}")
            return True


# 全局实例
redis_rate_limiter = RedisRateLimiter()
