"""T7-B4a (P1-2): 登录专项限流 + 账户锁定。

**为什么单独一个 guard**：
- 全局 `RateLimitMiddleware` 是 100 req/60s per IP，对密码喷洒来说太宽——
  攻击者每分钟能试 100 个密码。登录端点需要**更严格**的 IP 限流
  （默认 5/min）和**独立的用户名级**限流（10/15min），以及**账户锁定**
  机制（连续失败 5 次锁 15 分钟）。
- 复用现成的 ``RedisRateLimiter``（滑动窗口 Lua 原子）+ 简单计数 key。

**语义**：
- ``check_ip_rate(ip)``：每 IP 每分钟 5 次
- ``check_user_rate(username)``：每用户名每 15 分钟 10 次
- ``is_account_locked(username)``：查看当前是否处于锁定期
- ``record_failure(username)``：写失败计数；超阈值触发锁定
- ``clear_failures(username)``：登录成功后清计数
"""

from __future__ import annotations

import time

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.rate_limiter import redis_rate_limiter


_FAIL_KEY_PREFIX = "login:fail:"
_LOCK_KEY_PREFIX = "login:lock:"


def _fail_key(username: str) -> str:
    return f"{_FAIL_KEY_PREFIX}{username.lower()}"


def _lock_key(username: str) -> str:
    return f"{_LOCK_KEY_PREFIX}{username.lower()}"


async def check_ip_rate(ip: str) -> bool:
    """True = 通过；False = 该 IP 已被限流。

    P1-06 契约:``ip`` **必须** 是经过受信代理白名单解析后的最终客户端 IP,
    而不是 ``request.client.host`` 的 socket 对端地址。生产环境 Nginx / LB
    的对端 IP 对所有用户都相同,如果直接拿 socket IP 做限流键,一个用户
    很快就把整个入口的 IP 桶(默认 5/min)打爆,导致其他用户无法登录。
    调用方(如 ``routes/v1/base.py:login``)应先用
    ``antcode_web_api.middleware.middleware.get_client_ip`` 拿真实客户端 IP,
    再传进来。此处不再回退到 ``request.client.host``,避免绕过白名单校验。
    """
    if not ip:
        return True
    identifier = f"login:ip:{ip}"
    return await redis_rate_limiter.is_allowed(
        identifier,
        limit=settings.LOGIN_RATE_LIMIT_IP_MAX,
        period=settings.LOGIN_RATE_LIMIT_IP_PERIOD,
    )


async def check_user_rate(username: str) -> bool:
    """True = 通过；False = 该用户名已被限流。"""
    if not username:
        return True
    identifier = f"login:user:{username.lower()}"
    return await redis_rate_limiter.is_allowed(
        identifier,
        limit=settings.LOGIN_RATE_LIMIT_USER_MAX,
        period=settings.LOGIN_RATE_LIMIT_USER_PERIOD,
    )


async def is_account_locked(username: str) -> tuple[bool, int]:
    """返回 (锁定中?, 剩余秒数)。"""
    if not username:
        return False, 0
    try:
        redis = await get_redis_client()
        ttl = await redis.ttl(_lock_key(username))
        if ttl and ttl > 0:
            return True, int(ttl)
    except Exception as exc:
        logger.debug(f"is_account_locked 检查失败（放行）: {exc}")
    return False, 0


async def record_failure(username: str) -> tuple[int, bool]:
    """登录失败计数 +1；返回 (当前失败次数, 是否触发新锁定)。"""
    if not username:
        return 0, False
    try:
        redis = await get_redis_client()
        key = _fail_key(username)
        count = await redis.incr(key)
        count = int(count or 0)
        # 首次设置窗口 TTL —— 与锁定持续时间一致，锁定结束自然重置计数
        if count == 1:
            await redis.expire(key, settings.LOGIN_LOCKOUT_DURATION_SEC)
        newly_locked = False
        if count >= settings.LOGIN_LOCKOUT_FAILURES:
            # 触发锁定：写 lock key + 清失败计数
            lock_key = _lock_key(username)
            existed = await redis.exists(lock_key)
            await redis.set(
                lock_key,
                int(time.time()),
                ex=settings.LOGIN_LOCKOUT_DURATION_SEC,
            )
            newly_locked = not existed
            await redis.delete(key)  # 锁定后重置失败计数
            if newly_locked:
                logger.warning(
                    f"账户锁定: username={username} failures={count} "
                    f"lock_duration={settings.LOGIN_LOCKOUT_DURATION_SEC}s"
                )
        return count, newly_locked
    except Exception as exc:
        logger.warning(f"record_failure 失败: {exc}")
        return 0, False


async def clear_failures(username: str) -> None:
    """登录成功后清失败计数（不清锁定 —— 锁定期内成功也不解锁）。"""
    if not username:
        return
    try:
        redis = await get_redis_client()
        await redis.delete(_fail_key(username))
    except Exception as exc:
        logger.debug(f"clear_failures 失败: {exc}")


__all__ = [
    "check_ip_rate",
    "check_user_rate",
    "is_account_locked",
    "record_failure",
    "clear_failures",
]
