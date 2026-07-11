"""Redis 连接池管理器

提供 Redis 连接池的创建、管理和健康检查功能。
"""

import asyncio
import contextlib
import os
import platform
import socket
import time
from collections.abc import Awaitable
from typing import Optional, cast

import redis.asyncio as redis
from loguru import logger
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError

from antcode_core.common.config import settings
from antcode_core.common.exceptions import RedisConnectionError
from antcode_core.infrastructure.redis.factory import create_async_redis_client


class RedisConnectionPool:
    """Redis 连接池管理器"""

    _instance: Optional["RedisConnectionPool"] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.pool: redis.ConnectionPool | None = None
        self.redis_client: redis.Redis | None = None
        self._connected = False
        self._health_check_task: asyncio.Task | None = None
        self._last_health_check = 0.0
        self._health_check_interval = 5.0

    @classmethod
    async def get_instance(cls) -> "RedisConnectionPool":
        """获取单例实例"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.connect()
        return cls._instance

    async def connect(self) -> None:
        """建立 Redis 连接"""
        if self._connected and self.redis_client:
            return

        if not settings.REDIS_URL:
            raise RedisConnectionError("REDIS_URL 未配置")

        try:
            retry = Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=3)
            pool_kwargs = {
                "max_connections": 50,
                "retry_on_timeout": True,
                "retry": retry,
                "retry_on_error": [
                    ConnectionError,
                    TimeoutError,
                ],
                "socket_timeout": 10,
                "socket_connect_timeout": 10,
                "socket_keepalive": True,
                "health_check_interval": 30,
                "encoding": "utf-8",
                "decode_responses": False,
            }

            # Linux 特定的 keepalive 选项
            if platform.system() == "Linux":
                keepalive_options = {}
                if hasattr(socket, "TCP_KEEPIDLE"):
                    keepalive_options[socket.TCP_KEEPIDLE] = 60
                if hasattr(socket, "TCP_KEEPINTVL"):
                    keepalive_options[socket.TCP_KEEPINTVL] = 15
                if hasattr(socket, "TCP_KEEPCNT"):
                    keepalive_options[socket.TCP_KEEPCNT] = 4
                if keepalive_options:
                    pool_kwargs["socket_keepalive_options"] = keepalive_options

            # T6-T1: 走统一 factory，standalone/cluster/sentinel 自动分派
            self.redis_client = create_async_redis_client(
                settings.REDIS_URL,
                max_connections=50,
                decode_responses=False,
            )
            # cluster/sentinel 客户端没有暴露 pool，只有 standalone 才存
            self.pool = getattr(self.redis_client, "connection_pool", None)

            await cast(Awaitable[bool], self.redis_client.ping())
            self._connected = True
            self._last_health_check = time.monotonic()

            info = await self.redis_client.info()
            redis_version = info.get("redis_version", "unknown")
            logger.info(f"Redis 连接池已初始化 (版本 {redis_version}, 最大连接=50)")

            await self._start_health_check()

        except redis.AuthenticationError:
            error_msg = "Redis 认证失败: 密码错误或未配置认证"
            logger.warning(error_msg)
            raise RedisConnectionError(error_msg)
        except redis.ConnectionError:
            redis_host = settings.REDIS_URL.split("@")[-1] if "@" in settings.REDIS_URL else settings.REDIS_URL
            error_msg = f"无法连接 Redis ({redis_host}): 请检查 Redis 服务是否启动"
            logger.warning(error_msg)
            raise RedisConnectionError(error_msg)
        except Exception as e:
            error_msg = f"Redis 连接池初始化失败: {e}"
            logger.warning(error_msg)
            raise RedisConnectionError(error_msg)

    async def get_client(self) -> redis.Redis:
        """获取 Redis 客户端"""
        if self._connected and self.redis_client:
            now = time.monotonic()
            if now - self._last_health_check >= self._health_check_interval:
                try:
                    await cast(Awaitable[bool], self.redis_client.ping())
                    self._last_health_check = now
                    return self.redis_client
                except Exception:
                    self._connected = False

        if not self._connected or not self.redis_client:
            await self.connect()

        if not self.redis_client:
            raise RedisConnectionError("Redis 客户端未初始化")

        return self.redis_client

    async def is_connected(self) -> bool:
        """检查连接状态"""
        if not self._connected or not self.redis_client:
            return False

        try:
            await cast(Awaitable[bool], self.redis_client.ping())
            return True
        except Exception:
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        try:
            if self._health_check_task and not self._health_check_task.done():
                self._health_check_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._health_check_task

            if self.redis_client:
                await self.redis_client.close()

            if self.pool:
                await self.pool.disconnect()

            self._connected = False
            self.redis_client = None
            self.pool = None

            logger.info("Redis 连接池已关闭")

        except Exception as e:
            logger.exception(f"关闭 Redis 连接池失败: {e}")

    async def _start_health_check(self) -> None:
        """启动健康检查任务"""
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while True:
            try:
                await asyncio.sleep(30)

                if self.redis_client:
                    await cast(Awaitable[bool], self.redis_client.ping())
                    logger.debug("Redis 健康检查通过")
                else:
                    logger.warning("Redis 客户端为空，跳过健康检查")

            except asyncio.CancelledError:
                logger.debug("Redis 健康检查任务已取消")
                break
            except Exception as e:
                logger.exception(f"Redis 健康检查失败: {e}")
                self._connected = False
                try:
                    await self.connect()
                    logger.info("Redis 连接已恢复")
                except Exception as reconnect_error:
                    logger.exception(f"Redis 重连失败: {reconnect_error}")

    async def get_pool_stats(self) -> dict:
        """获取连接池统计信息"""
        if not self.pool:
            return {"error": "连接池未初始化"}

        try:
            available_connections = len(self.pool._available_connections)
            in_use_connections = len(self.pool._in_use_connections)
            stats = {
                "created_connections": available_connections + in_use_connections,
                "available_connections": available_connections,
                "in_use_connections": in_use_connections,
                "max_connections": self.pool.max_connections,
                "is_connected": self._connected,
            }
            return stats
        except Exception as e:
            return {"error": f"获取统计信息失败: {e}"}

    @classmethod
    async def cleanup(cls) -> None:
        """清理单例实例"""
        if cls._instance:
            await cls._instance.disconnect()
            cls._instance = None


async def get_redis_client() -> redis.Redis:
    """获取 Redis 客户端（便捷函数，单例 hot pool）"""
    pool_manager = await RedisConnectionPool.get_instance()
    return await pool_manager.get_client()


async def close_redis_pool() -> None:
    """关闭 Redis 连接池（便捷函数）"""
    await RedisConnectionPool.cleanup()


# ---------------------------------------------------------------------------
# P5.2: hot / cold connection pool helpers
# ---------------------------------------------------------------------------
# 不同业务场景对 Redis 连接数的需求差异显著：
#   - hot：数据面（result/log Stream、任务分发等高吞吐路径）
#   - cold：控制面（scheduler/reconcile/audit 等低频路径）
# 拆开后可以避免控制面被数据面挤占连接，也方便独立调参。
#
# Hot pool 上限默认从 50 提到 100：实际跑过爬虫 + 调度 + 心跳的压测显示,
# Master ingester + Gateway 写入路径并发到 80+ 时连接池等待会成为新的尾延迟
# 来源。100 仍远低于 Redis 默认 maxclients=10000,且通过
# ``REDIS_HOT_POOL_MAX_CONN`` 环境变量可在部署侧再调整。
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value > 0 else default


HOT_POOL_DEFAULT = _env_int("REDIS_HOT_POOL_MAX_CONN", 100)
COLD_POOL_DEFAULT = _env_int("REDIS_COLD_POOL_MAX_CONN", 10)


def _build_pool_kwargs(max_connections: int, **overrides) -> dict:
    """构建符合项目默认配置的 ConnectionPool kwargs"""
    retry = Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=3)
    pool_kwargs: dict = {
        "max_connections": max_connections,
        "retry_on_timeout": True,
        "retry": retry,
        "retry_on_error": [ConnectionError, TimeoutError],
        "socket_timeout": 10,
        "socket_connect_timeout": 10,
        "socket_keepalive": True,
        "health_check_interval": 30,
        "encoding": "utf-8",
        "decode_responses": False,
    }

    if platform.system() == "Linux":
        keepalive_options = {}
        if hasattr(socket, "TCP_KEEPIDLE"):
            keepalive_options[socket.TCP_KEEPIDLE] = 60
        if hasattr(socket, "TCP_KEEPINTVL"):
            keepalive_options[socket.TCP_KEEPINTVL] = 15
        if hasattr(socket, "TCP_KEEPCNT"):
            keepalive_options[socket.TCP_KEEPCNT] = 4
        if keepalive_options:
            pool_kwargs["socket_keepalive_options"] = keepalive_options

    pool_kwargs.update(overrides)
    return pool_kwargs


def _make_client(
    url: str | None,
    *,
    max_connections: int,
    pool_label: str,
    **overrides,
) -> redis.Redis:
    """创建独立的 Redis 客户端（不走单例）。

    T6-T1: 走统一 factory，standalone/cluster/sentinel 自动分派。
    集群模式下 max_connections 是每节点独立池上限。
    """
    redis_url = url or settings.REDIS_URL
    if not redis_url:
        raise RedisConnectionError("REDIS_URL 未配置")

    client = create_async_redis_client(
        redis_url,
        max_connections=max_connections,
        decode_responses=False,
        **overrides,
    )
    logger.debug(f"Redis {pool_label} 客户端已创建 (max_connections={max_connections})")
    return client


def make_hot_client(
    url: str | None = None,
    *,
    max_connections: int = HOT_POOL_DEFAULT,
    **kwargs,
) -> redis.Redis:
    """高吞吐场景使用 - Master ingester 组、Gateway 写入路径等"""
    return _make_client(url, max_connections=max_connections, pool_label="hot", **kwargs)


def make_cold_client(
    url: str | None = None,
    *,
    max_connections: int = COLD_POOL_DEFAULT,
    **kwargs,
) -> redis.Redis:
    """低频场景使用 - Master control 组、辅助查询等"""
    return _make_client(url, max_connections=max_connections, pool_label="cold", **kwargs)
