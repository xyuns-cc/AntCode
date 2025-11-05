"""
统一缓存系统
支持Redis和内存缓存的统一配置和管理
"""

import asyncio
import time
import weakref
from dataclasses import dataclass
from datetime import datetime
from typing import Dict

import ujson
from loguru import logger

from src.core.config import settings


@dataclass
class CacheConfig:
    """统一缓存配置"""
    # 缓存类型配置
    use_redis: bool = True
    fallback_to_memory: bool = True
    
    # 缓存时间配置
    default_ttl: int = 300  # 默认5分钟
    max_ttl: int = 3600    # 最大1小时
    min_ttl: int = 10      # 最小10秒
    
    # 内存缓存配置
    memory_max_size: int = 1000  # 内存缓存最大条目数
    memory_cleanup_threshold: float = 0.9  # 清理阈值
    
    # Redis配置
    redis_key_prefix: str = "cache:"
    redis_connection_timeout: int = 5
    
    # 性能配置
    enable_compression: bool = False  # 大对象压缩
    compression_threshold: int = 1024  # 压缩阈值(字节)
    
    @classmethod
    def from_settings(cls, prefix = ""):
        """从settings创建配置"""
        # 使用统一的缓存配置
        return cls(
            use_redis=getattr(settings, 'CACHE_USE_REDIS', True),
            fallback_to_memory=getattr(settings, 'CACHE_FALLBACK_TO_MEMORY', True),
            default_ttl=getattr(settings, 'CACHE_DEFAULT_TTL', 300),
            redis_key_prefix=f"cache:{prefix.lower().rstrip('_')}:" if prefix else "cache:",
        )


class UnifiedCache:
    """统一缓存管理器"""
    
    def __init__(self, config, name = "default"):
        self.config = config
        self.name = name
        self._redis_client = None
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._weak_refs: Dict[str, weakref.ReferenceType] = {}
        
        # 统计信息
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
            "errors": 0
        }
        
        logger.info(f"初始化缓存 '{name}': Redis={config.use_redis}, TTL={config.default_ttl}s")
    
    async def _get_redis_client(self):
        """获取Redis客户端"""
        if not self._redis_client and self.config.use_redis:
            try:
                from src.utils.redis_pool import get_redis_client
                self._redis_client = await asyncio.wait_for(
                    get_redis_client(),
                    timeout=self.config.redis_connection_timeout
                )
                logger.debug(f"缓存 '{self.name}' 连接Redis成功")
                return self._redis_client
            except Exception as e:
                logger.warning(f"缓存 '{self.name}' Redis连接失败: {e}")
                if self.config.fallback_to_memory:
                    logger.info(f"缓存 '{self.name}' 切换到内存模式")
                else:
                    raise
        return self._redis_client
    
    def _serialize_value(self, value):
        """序列化值"""
        try:
            # 自定义序列化处理datetime等对象
            def default_serializer(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                elif hasattr(obj, '__dict__'):
                    return obj.__dict__
                else:
                    return str(obj)
            
            data = ujson.dumps(value, ensure_ascii=False, default=default_serializer)
            if self.config.enable_compression and len(data) > self.config.compression_threshold:
                import gzip
                return gzip.compress(data.encode('utf-8'))
            return data.encode('utf-8')
        except Exception as e:
            logger.error(f"缓存序列化失败: {e}")
            raise
    
    def _deserialize_value(self, data):
        """反序列化值"""
        try:
            if self.config.enable_compression:
                try:
                    import gzip
                    data = gzip.decompress(data)
                except:
                    pass  # 如果不是压缩数据，继续处理
            
            return ujson.loads(data.decode('utf-8'))
        except Exception as e:
            logger.error(f"缓存反序列化失败: {e}")
            return None
    
    def _generate_key(self, key):
        """生成完整的缓存键"""
        return f"{self.config.redis_key_prefix}{key}"
    
    def _is_expired(self, cached_item):
        """检查缓存项是否过期"""
        if 'expires_at' not in cached_item:
            return True
        return time.time() > cached_item['expires_at']
    
    def _cleanup_memory_cache(self):
        """清理内存缓存"""
        if len(self._memory_cache) <= self.config.memory_max_size:
            return
        
        # 移除过期的缓存项
        expired_keys = [
            key for key, item in self._memory_cache.items() 
            if self._is_expired(item)
        ]
        for key in expired_keys:
            del self._memory_cache[key]
            self._weak_refs.pop(key, None)
        
        # 如果还是太多，移除最旧的
        if len(self._memory_cache) > self.config.memory_max_size * self.config.memory_cleanup_threshold:
            items = list(self._memory_cache.items())
            items.sort(key=lambda x: x[1].get('created_at', 0))
            
            remove_count = len(items) - int(self.config.memory_max_size * self.config.memory_cleanup_threshold)
            for key, _ in items[:remove_count]:
                del self._memory_cache[key]
                self._weak_refs.pop(key, None)
    
    async def get(self, key):
        """获取缓存值"""
        full_key = self._generate_key(key)
        
        try:
            # 优先从Redis获取
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        data = await redis_client.get(full_key)
                        if data:
                            value = self._deserialize_value(data)
                            self._stats["hits"] += 1
                            logger.debug(f"缓存 '{self.name}' Redis命中: {key}")
                            return value
                    except Exception as e:
                        logger.error(f"Redis读取失败: {e}")
                        self._stats["errors"] += 1
            
            # 从内存缓存获取
            if key in self._memory_cache:
                cached_item = self._memory_cache[key]
                if not self._is_expired(cached_item):
                    self._stats["hits"] += 1
                    logger.debug(f"缓存 '{self.name}' 内存命中: {key}")
                    return cached_item['value']
                else:
                    # 清理过期项
                    del self._memory_cache[key]
                    self._weak_refs.pop(key, None)
            
            self._stats["misses"] += 1
            return None
            
        except Exception as e:
            logger.error(f"缓存获取失败: {e}")
            self._stats["errors"] += 1
            return None
    
    async def set(self, key, value, ttl = None):
        """设置缓存值"""
        if ttl is None:
            ttl = self.config.default_ttl
        
        # 限制TTL范围
        ttl = max(self.config.min_ttl, min(ttl, self.config.max_ttl))
        
        full_key = self._generate_key(key)
        success = False
        
        try:
            # 保存到Redis
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        data = self._serialize_value(value)
                        await redis_client.setex(full_key, ttl, data)
                        success = True
                        logger.debug(f"缓存 '{self.name}' Redis保存: {key}")
                    except Exception as e:
                        logger.error(f"Redis写入失败: {e}")
                        self._stats["errors"] += 1
            
            # 保存到内存缓存（作为备份或主要存储）
            if not success or self.config.fallback_to_memory:
                self._cleanup_memory_cache()
                
                cached_item = {
                    'value': value,
                    'created_at': time.time(),
                    'expires_at': time.time() + ttl,
                    'ttl': ttl
                }
                self._memory_cache[key] = cached_item
                
                # 使用弱引用避免循环引用
                if hasattr(value, '__weakref__'):
                    def cleanup_callback(ref):
                        self._memory_cache.pop(key, None)
                        self._weak_refs.pop(key, None)
                    
                    self._weak_refs[key] = weakref.ref(value, cleanup_callback)
                
                success = True
                logger.debug(f"缓存 '{self.name}' 内存保存: {key}")
            
            if success:
                self._stats["sets"] += 1
                
            return success
            
        except Exception as e:
            logger.error(f"缓存设置失败: {e}")
            self._stats["errors"] += 1
            return False
    
    async def delete(self, key):
        """删除缓存项"""
        full_key = self._generate_key(key)
        success = False
        
        try:
            # 从Redis删除
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        result = await redis_client.delete(full_key)
                        success = result > 0
                        logger.debug(f"缓存 '{self.name}' Redis删除: {key}")
                    except Exception as e:
                        logger.error(f"Redis删除失败: {e}")
                        self._stats["errors"] += 1
            
            # 从内存删除
            if key in self._memory_cache:
                del self._memory_cache[key]
                self._weak_refs.pop(key, None)
                success = True
                logger.debug(f"缓存 '{self.name}' 内存删除: {key}")
            
            if success:
                self._stats["deletes"] += 1
                
            return success
            
        except Exception as e:
            logger.error(f"缓存删除失败: {e}")
            self._stats["errors"] += 1
            return False
    
    async def clear(self):
        """清空所有缓存"""
        success = False

        try:
            # 清空Redis
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        pattern = f"{self.config.redis_key_prefix}*"
                        # 使用SCAN替代KEYS，分批删除
                        batch: list = []
                        batch_size = 500
                        # 优先使用scan_iter，如不可用则回退到keys
                        if hasattr(redis_client, "scan_iter"):
                            async for k in redis_client.scan_iter(match=pattern, count=batch_size):
                                batch.append(k)
                                if len(batch) >= batch_size:
                                    await redis_client.delete(*batch)
                                    batch.clear()
                            if batch:
                                await redis_client.delete(*batch)
                        else:
                            keys = await redis_client.keys(pattern)
                            if keys:
                                # 仍尽量分批删除
                                for i in range(0, len(keys), batch_size):
                                    await redis_client.delete(*keys[i:i+batch_size])
                        success = True
                        logger.info(f"缓存 '{self.name}' Redis已清空")
                    except Exception as e:
                        logger.error(f"Redis清空失败: {e}")
                        self._stats["errors"] += 1
            
            # 清空内存
            self._memory_cache.clear()
            self._weak_refs.clear()
            success = True
            logger.info(f"缓存 '{self.name}' 内存已清空")
            
            return success
            
        except Exception as e:
            logger.error(f"缓存清空失败: {e}")
            self._stats["errors"] += 1
            return False

    async def clear_prefix(self, key_prefix: str):
        """按前缀清理缓存（仅清理当前命名空间内匹配的键）。

        说明：
        - Redis: 删除匹配 pattern = redis_key_prefix + key_prefix + '*'
        - 内存: 删除 _memory_cache 中以 key_prefix 开头的键
        """
        success = False
        try:
            # Redis 按前缀清理
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        pattern = f"{self.config.redis_key_prefix}{key_prefix}*"
                        batch: list = []
                        batch_size = 500
                        if hasattr(redis_client, "scan_iter"):
                            async for k in redis_client.scan_iter(match=pattern, count=batch_size):
                                batch.append(k)
                                if len(batch) >= batch_size:
                                    await redis_client.delete(*batch)
                                    batch.clear()
                            if batch:
                                await redis_client.delete(*batch)
                        else:
                            keys = await redis_client.keys(pattern)
                            if keys:
                                for i in range(0, len(keys), batch_size):
                                    await redis_client.delete(*keys[i:i+batch_size])
                        success = True
                        logger.info(f"缓存 '{self.name}' Redis前缀清理(使用SCAN): {key_prefix}")
                    except Exception as e:
                        logger.error(f"Redis前缀清理失败: {e}")
                        self._stats["errors"] += 1

            # 内存按前缀清理
            to_delete = [k for k in list(self._memory_cache.keys()) if k.startswith(key_prefix)]
            for k in to_delete:
                self._memory_cache.pop(k, None)
                self._weak_refs.pop(k, None)
                success = True or success
            if to_delete:
                logger.info(f"缓存 '{self.name}' 内存前缀清理: {key_prefix} 共{len(to_delete)}项")

            if success:
                self._stats["deletes"] += 1
            return success
        except Exception as e:
            logger.error(f"缓存前缀清理失败: {e}")
            self._stats["errors"] += 1
            return False
    
    async def exists(self, key):
        """检查缓存项是否存在"""
        return await self.get(key) is not None
    
    async def get_stats(self):
        """获取缓存统计信息"""
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "name": self.name,
            "config": {
                "use_redis": self.config.use_redis,
                "default_ttl": self.config.default_ttl,
                "memory_max_size": self.config.memory_max_size
            },
            "stats": {
                **self._stats,
                "hit_rate": round(hit_rate, 2),
                "memory_items": len(self._memory_cache),
                "weak_refs": len(self._weak_refs)
            },
            "redis_available": self._redis_client is not None
        }


class CacheManager:
    """全局缓存管理器"""
    
    def __init__(self):
        self._caches: Dict[str, UnifiedCache] = {}
        self._default_config = CacheConfig()
    
    def get_cache(self, name, config = None):
        """获取或创建缓存实例"""
        if name not in self._caches:
            if config is None:
                config = self._default_config
            self._caches[name] = UnifiedCache(config, name)
        return self._caches[name]
    
    def create_cache(self, name, **config_kwargs):
        """创建新的缓存实例"""
        config = CacheConfig(**config_kwargs)
        cache = UnifiedCache(config, name)
        self._caches[name] = cache
        return cache
    
    async def clear_all(self):
        """清空所有缓存"""
        for cache in self._caches.values():
            await cache.clear()
    
    async def get_all_stats(self):
        """获取所有缓存的统计信息"""
        stats = {}
        for name, cache in self._caches.items():
            stats[name] = await cache.get_stats()
        return stats
    
    def list_caches(self):
        """列出所有缓存名称"""
        return list(self._caches.keys())


# 全局缓存管理器实例
cache_manager = CacheManager()

# 便捷函数
def get_cache(name = "default", **config_kwargs):
    """获取缓存实例的便捷函数"""
    if config_kwargs:
        config = CacheConfig(**config_kwargs)
        return cache_manager.get_cache(name, config)
    return cache_manager.get_cache(name)


# 预定义的缓存实例 - 使用统一配置
user_cache = get_cache(
    "users",
    use_redis=getattr(settings, 'CACHE_USE_REDIS', True),
    fallback_to_memory=getattr(settings, 'CACHE_FALLBACK_TO_MEMORY', True),
    default_ttl=getattr(settings, 'USERS_CACHE_TTL', 300),
    redis_key_prefix="cache:users:"
)

metrics_cache = get_cache(
    "metrics", 
    use_redis=getattr(settings, 'CACHE_USE_REDIS', True),
    fallback_to_memory=getattr(settings, 'CACHE_FALLBACK_TO_MEMORY', True),
    default_ttl=getattr(settings, 'METRICS_CACHE_TTL', 30),
    redis_key_prefix="cache:metrics:"
)

api_cache = get_cache(
    "api",
    use_redis=getattr(settings, 'CACHE_USE_REDIS', True),
    fallback_to_memory=getattr(settings, 'CACHE_FALLBACK_TO_MEMORY', True),
    default_ttl=getattr(settings, 'API_CACHE_TTL', 300),
    redis_key_prefix="cache:api:"
)

query_cache = get_cache(
    "queries",
    use_redis=getattr(settings, 'CACHE_USE_REDIS', True),
    fallback_to_memory=getattr(settings, 'CACHE_FALLBACK_TO_MEMORY', True),
    default_ttl=getattr(settings, 'QUERY_CACHE_TTL', 300),
    redis_key_prefix="cache:queries:"
)
