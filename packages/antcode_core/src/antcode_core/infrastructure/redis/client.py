"""Redis 连接池管理器

提供 Redis 连接池的创建、管理和健康检查功能。
"""

import asyncio
import contextlib
import platform
import socket
import time
from typing import Optional

import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError
from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.exceptions import RedisConnectionError


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

            self.pool = redis.ConnectionPool.from_url(settings.REDIS_URL, **pool_kwargs)
            self.redis_client = redis.Redis(connection_pool=self.pool)

            await self.redis_client.ping()
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
            redis_host = (
                settings.REDIS_URL.split("@")[-1]
                if "@" in settings.REDIS_URL
                else settings.REDIS_URL
            )
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
                    await self.redis_client.ping()
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
            await self.redis_client.ping()
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
            logger.error(f"关闭 Redis 连接池失败: {e}")

    async def _start_health_check(self) -> None:
        """启动健康检查任务"""
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while True:
            try:
                await asyncio.sleep(30)

                if self.redis_client:
                    await self.redis_client.ping()
                    logger.debug("Redis 健康检查通过")
                else:
                    logger.warning("Redis 客户端为空，跳过健康检查")

            except asyncio.CancelledError:
                logger.debug("Redis 健康检查任务已取消")
                break
            except Exception as e:
                logger.error(f"Redis 健康检查失败: {e}")
                self._connected = False
                try:
                    await self.connect()
                    logger.info("Redis 连接已恢复")
                except Exception as reconnect_error:
                    logger.error(f"Redis 重连失败: {reconnect_error}")

    async def get_pool_stats(self) -> dict:
        """获取连接池统计信息"""
        if not self.pool:
            return {"error": "连接池未初始化"}

        try:
            stats = {
                "created_connections": self.pool.created_connections,
                "available_connections": len(self.pool._available_connections),
                "in_use_connections": len(self.pool._in_use_connections),
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
    """获取 Redis 客户端（便捷函数）"""
    pool_manager = await RedisConnectionPool.get_instance()
    return await pool_manager.get_client()


async def close_redis_pool() -> None:
    """关闭 Redis 连接池（便捷函数）"""
    await RedisConnectionPool.cleanup()
