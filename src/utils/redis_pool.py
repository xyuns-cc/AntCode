"""
Redis连接池管理器
提供高效的Redis连接复用和管理
"""

import asyncio

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
        """获取单例实例"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.connect()
        return cls._instance
    
    async def connect(self):
        """建立Redis连接池"""
        try:
            # 解析Redis URL
            url_parts = redis.from_url(settings.REDIS_URL, decode_responses=False)
            
            # 创建连接池
            self.pool = redis.ConnectionPool.from_url(
                settings.REDIS_URL,
                # 连接池配置
                max_connections=20,
                retry_on_timeout=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                health_check_interval=30,
                # 编码配置
                encoding="utf-8",
                decode_responses=False
            )
            
            # 创建Redis客户端
            self.redis_client = redis.Redis(connection_pool=self.pool)
            
            # 测试连接
            await self.redis_client.ping()
            self._connected = True
            
            # 获取Redis信息
            info = await self.redis_client.info()
            redis_version = info.get('redis_version', 'unknown')
            logger.info(f"✅ Redis连接池初始化成功 (版本: {redis_version})")
            logger.info(f"   连接池最大连接数: 20")
            logger.info(f"   健康检查间隔: 30秒")
            
            # 启动健康检查任务
            await self._start_health_check()
            
        except redis.AuthenticationError as e:
            error_msg = f"Redis认证失败: {e}"
            logger.error(f"❌ {error_msg}")
            raise RedisConnectionException(error_msg)
        except redis.ConnectionError as e:
            error_msg = f"Redis连接失败: {e}"
            logger.error(f"❌ {error_msg}")
            raise RedisConnectionException(error_msg)
        except Exception as e:
            error_msg = f"Redis连接池初始化失败: {e}"
            logger.error(f"❌ {error_msg}")
            raise RedisConnectionException(error_msg)
    
    async def get_client(self):
        """获取Redis客户端"""
        if not self._connected or not self.redis_client:
            await self.connect()
        
        if not self.redis_client:
            raise RedisConnectionException("Redis客户端未初始化")
            
        return self.redis_client
    
    async def is_connected(self):
        """检查连接状态"""
        if not self._connected or not self.redis_client:
            return False
            
        try:
            await self.redis_client.ping()
            return True
        except Exception:
            self._connected = False
            return False
    
    async def disconnect(self):
        """断开连接池"""
        try:
            # 停止健康检查
            if self._health_check_task and not self._health_check_task.done():
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass
            
            # 关闭Redis客户端
            if self.redis_client:
                await self.redis_client.close()
                
            # 断开连接池
            if self.pool:
                await self.pool.disconnect()
                
            self._connected = False
            self.redis_client = None
            self.pool = None
            
            logger.info("✅ Redis连接池已安全关闭")
            
        except Exception as e:
            logger.error(f"关闭Redis连接池时出错: {e}")
    
    async def _start_health_check(self):
        """启动健康检查任务"""
        self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def _health_check_loop(self):
        """健康检查循环"""
        while True:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                
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
                # 尝试重新连接
                try:
                    await self.connect()
                    logger.info("Redis连接已恢复")
                except Exception as reconnect_error:
                    logger.error(f"Redis重新连接失败: {reconnect_error}")
    
    async def get_pool_stats(self):
        """获取连接池统计信息"""
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
        """清理单例实例"""
        if cls._instance:
            await cls._instance.disconnect()
            cls._instance = None


# 提供便捷的全局函数
async def get_redis_client():
    """获取Redis客户端的便捷函数"""
    pool_manager = await RedisConnectionPool.get_instance()
    return await pool_manager.get_client()


async def close_redis_pool():
    """关闭Redis连接池的便捷函数"""
    await RedisConnectionPool.cleanup()