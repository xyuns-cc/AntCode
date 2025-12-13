"""Redis连接池管理器"""

import asyncio
import platform
import socket

import redis.asyncio as redis
from loguru import logger

from src.core.config import settings
from src.core.exceptions import RedisConnectionException


class RedisConnectionPool:
    """Redis连接池管理器"""

    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.pool = None
        self.redis_client = None
        self._connected = False
        self._health_check_task = None

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.connect()
        return cls._instance

    async def connect(self):
        if self._connected and self.redis_client:
            return

        try:
            pool_kwargs = {
                "max_connections": 50,
                "retry_on_timeout": True,
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

            self.pool = redis.ConnectionPool.from_url(settings.REDIS_URL, **pool_kwargs)
            self.redis_client = redis.Redis(connection_pool=self.pool)

            await self.redis_client.ping()
            self._connected = True

            info = await self.redis_client.info()
            redis_version = info.get('redis_version', 'unknown')
            logger.info(f"Redis连接池已初始化 (版本{redis_version}, 最大连接=50)")

            await self._start_health_check()

        except redis.AuthenticationError as e:
            error_msg = f"Redis认证失败: 密码错误或未配置认证"
            logger.warning(error_msg)
            raise RedisConnectionException(error_msg)
        except redis.ConnectionError as e:
            # 提取更友好的错误信息
            redis_host = settings.REDIS_URL.split('@')[-1] if '@' in settings.REDIS_URL else settings.REDIS_URL
            error_msg = f"无法连接Redis ({redis_host}): 请检查Redis服务是否启动"
            logger.warning(error_msg)
            raise RedisConnectionException(error_msg)
        except Exception as e:
            error_msg = f"Redis连接池初始化失败: {e}"
            logger.warning(error_msg)
            raise RedisConnectionException(error_msg)

    async def get_client(self):
        if not self._connected or not self.redis_client:
            await self.connect()

        if not self.redis_client:
            raise RedisConnectionException("Redis客户端未初始化")

        return self.redis_client

    async def is_connected(self):
        if not self._connected or not self.redis_client:
            return False

        try:
            await self.redis_client.ping()
            return True
        except Exception:
            self._connected = False
            return False

    async def disconnect(self):
        try:
            if self._health_check_task and not self._health_check_task.done():
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass

            if self.redis_client:
                await self.redis_client.close()

            if self.pool:
                await self.pool.disconnect()

            self._connected = False
            self.redis_client = None
            self.pool = None

            logger.info("Redis连接池已关闭")

        except Exception as e:
            logger.error(f"关闭Redis连接池失败: {e}")

    async def _start_health_check(self):
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    async def _health_check_loop(self):
        while True:
            try:
                await asyncio.sleep(30)

                if self.redis_client:
                    await self.redis_client.ping()
                    logger.debug("Redis健康检查通过")
                else:
                    logger.warning("Redis客户端为空，跳过健康检查")

            except asyncio.CancelledError:
                logger.debug("Redis健康检查任务已取消")
                break
            except Exception as e:
                logger.error(f"Redis健康检查失败: {e}")
                self._connected = False
                try:
                    await self.connect()
                    logger.info("Redis连接已恢复")
                except Exception as reconnect_error:
                    logger.error(f"Redis重连失败: {reconnect_error}")

    async def get_pool_stats(self):
        if not self.pool:
            return {"error": "连接池未初始化"}

        try:
            stats = {
                "created_connections": self.pool.created_connections,
                "available_connections": len(self.pool._available_connections),
                "in_use_connections": len(self.pool._in_use_connections),
                "max_connections": self.pool.max_connections,
                "is_connected": self._connected
            }
            return stats
        except Exception as e:
            return {"error": f"获取统计信息失败: {e}"}

    @classmethod
    async def cleanup(cls):
        if cls._instance:
            await cls._instance.disconnect()
            cls._instance = None


async def get_redis_client():
    pool_manager = await RedisConnectionPool.get_instance()
    return await pool_manager.get_client()


async def close_redis_pool():
    await RedisConnectionPool.cleanup()
