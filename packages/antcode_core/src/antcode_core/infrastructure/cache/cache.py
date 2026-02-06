"""统一缓存系统"""

import asyncio
import time
import weakref
from dataclasses import dataclass

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.serialization import from_json, to_json


@dataclass
class CacheConfig:
    """缓存配置"""

    use_redis: bool = True
    default_ttl: int = 300
    max_ttl: int = 3600
    min_ttl: int = 10
    memory_max_size: int = 1000
    memory_cleanup_threshold: float = 0.9
    redis_key_prefix: str = "cache:"
    redis_connection_timeout: int = 5
    enable_compression: bool = False
    compression_threshold: int = 1024

    @classmethod
    def from_settings(cls, prefix=""):
        return cls(
            use_redis=getattr(settings, "CACHE_USE_REDIS", False),
            default_ttl=getattr(settings, "CACHE_DEFAULT_TTL", 300),
            redis_key_prefix=f"cache:{prefix.lower().rstrip('_')}:" if prefix else "cache:",
        )


class UnifiedCache:
    """统一缓存管理器"""

    def __init__(self, config, name="default"):
        self.config = config
        self.name = name
        self._redis_client = None
        self._memory_cache = {}
        self._weak_refs = {}

        self._stats = {"hits": 0, "misses": 0, "sets": 0, "deletes": 0, "errors": 0}

        logger.info(f"缓存 '{name}' 已初始化: Redis={config.use_redis}, TTL={config.default_ttl}s")

    async def _get_redis_client(self):
        if not self.config.use_redis:
            return None

        if not self._redis_client:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await asyncio.wait_for(
                    get_redis_client(), timeout=self.config.redis_connection_timeout
                )
                logger.info(f"缓存 '{self.name}' 已连接到Redis")
            except Exception as e:
                logger.error(f"缓存 '{self.name}' Redis连接失败: {e}")
                raise

        return self._redis_client

    def _serialize_value(self, value):
        try:
            data = to_json(value)
            if self.config.enable_compression and len(data) > self.config.compression_threshold:
                import gzip

                return gzip.compress(data.encode("utf-8"))
            return data.encode("utf-8")
        except Exception as e:
            logger.error(f"缓存序列化失败: {e}")
            raise

    def _deserialize_value(self, data):
        try:
            if self.config.enable_compression:
                try:
                    import gzip

                    data = gzip.decompress(data)
                except Exception:
                    pass

            return from_json(data.decode("utf-8"))
        except Exception as e:
            logger.error(f"缓存反序列化失败: {e}")
            return None

    def _generate_key(self, key):
        return f"{self.config.redis_key_prefix}{key}"

    def _is_expired(self, cached_item):
        if "expires_at" not in cached_item:
            return True
        return time.time() > cached_item["expires_at"]

    def _cleanup_memory_cache(self):
        if len(self._memory_cache) <= self.config.memory_max_size:
            return

        expired_keys = [key for key, item in self._memory_cache.items() if self._is_expired(item)]
        for key in expired_keys:
            del self._memory_cache[key]
            self._weak_refs.pop(key, None)

        if (
            len(self._memory_cache)
            > self.config.memory_max_size * self.config.memory_cleanup_threshold
        ):
            items = list(self._memory_cache.items())
            items.sort(key=lambda x: x[1].get("created_at", 0))

            remove_count = len(items) - int(
                self.config.memory_max_size * self.config.memory_cleanup_threshold
            )
            for key, _ in items[:remove_count]:
                del self._memory_cache[key]
                self._weak_refs.pop(key, None)

    async def get(self, key):
        full_key = self._generate_key(key)

        try:
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
                        raise
            else:
                if key in self._memory_cache:
                    cached_item = self._memory_cache[key]
                    if not self._is_expired(cached_item):
                        self._stats["hits"] += 1
                        logger.debug(f"缓存 '{self.name}' 内存命中: {key}")
                        return cached_item["value"]
                    else:
                        del self._memory_cache[key]
                        self._weak_refs.pop(key, None)

            self._stats["misses"] += 1
            return None

        except Exception as e:
            logger.error(f"缓存获取失败: {e}")
            self._stats["errors"] += 1
            raise

    async def set(self, key, value, ttl=None):
        if ttl is None:
            ttl = self.config.default_ttl

        ttl = max(self.config.min_ttl, min(ttl, self.config.max_ttl))

        full_key = self._generate_key(key)

        try:
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        data = self._serialize_value(value)
                        await redis_client.setex(full_key, ttl, data)
                        self._stats["sets"] += 1
                        logger.debug(f"缓存 '{self.name}' Redis设置: {key}")
                        return True
                    except Exception as e:
                        logger.error(f"Redis写入失败: {e}")
                        self._stats["errors"] += 1
                        raise
            else:
                self._cleanup_memory_cache()

                cached_item = {
                    "value": value,
                    "created_at": time.time(),
                    "expires_at": time.time() + ttl,
                    "ttl": ttl,
                }
                self._memory_cache[key] = cached_item

                if hasattr(value, "__weakref__"):

                    def cleanup_callback(ref):
                        self._memory_cache.pop(key, None)
                        self._weak_refs.pop(key, None)

                    self._weak_refs[key] = weakref.ref(value, cleanup_callback)

                self._stats["sets"] += 1
                logger.debug(f"缓存 '{self.name}' 内存设置: {key}")
                return True

        except Exception as e:
            logger.error(f"缓存设置失败: {e}")
            self._stats["errors"] += 1
            raise

    async def delete(self, key):
        full_key = self._generate_key(key)

        try:
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        result = await redis_client.delete(full_key)
                        self._stats["deletes"] += 1
                        logger.debug(f"缓存 '{self.name}' Redis删除: {key}")
                        return result > 0
                    except Exception as e:
                        logger.error(f"Redis删除失败: {e}")
                        self._stats["errors"] += 1
                        raise
            else:
                if key in self._memory_cache:
                    del self._memory_cache[key]
                    self._weak_refs.pop(key, None)
                    self._stats["deletes"] += 1
                    logger.debug(f"缓存 '{self.name}' 内存删除: {key}")
                    return True
                return False

        except Exception as e:
            logger.error(f"缓存删除失败: {e}")
            self._stats["errors"] += 1
            raise

    async def clear(self):
        try:
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        pattern = f"{self.config.redis_key_prefix}*"
                        batch = []
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
                                    await redis_client.delete(*keys[i : i + batch_size])
                        logger.info(f"缓存 '{self.name}' Redis已清空")
                        return True
                    except Exception as e:
                        logger.error(f"Redis清空失败: {e}")
                        self._stats["errors"] += 1
                        raise
            else:
                self._memory_cache.clear()
                self._weak_refs.clear()
                logger.info(f"缓存 '{self.name}' 内存已清空")
                return True

        except Exception as e:
            logger.error(f"缓存清空失败: {e}")
            self._stats["errors"] += 1
            raise

    async def clear_prefix(self, key_prefix):
        try:
            if self.config.use_redis:
                redis_client = await self._get_redis_client()
                if redis_client:
                    try:
                        pattern = f"{self.config.redis_key_prefix}{key_prefix}*"
                        batch = []
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
                                    await redis_client.delete(*keys[i : i + batch_size])
                        self._stats["deletes"] += 1
                        logger.info(f"缓存 '{self.name}' 前缀已清除: {key_prefix}")
                        return True
                    except Exception as e:
                        logger.error(f"Redis前缀清除失败: {e}")
                        self._stats["errors"] += 1
                        raise
            else:
                to_delete = [k for k in list(self._memory_cache.keys()) if k.startswith(key_prefix)]
                for k in to_delete:
                    self._memory_cache.pop(k, None)
                    self._weak_refs.pop(k, None)
                if to_delete:
                    self._stats["deletes"] += 1
                    logger.info(
                        f"缓存 '{self.name}' 前缀已清除: {key_prefix} ({len(to_delete)} items)"
                    )
                return True

        except Exception as e:
            logger.error(f"缓存前缀清除失败: {e}")
            self._stats["errors"] += 1
            raise

    async def exists(self, key):
        return await self.get(key) is not None

    async def get_stats(self):
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total_requests * 100) if total_requests > 0 else 0

        return {
            "name": self.name,
            "config": {
                "use_redis": self.config.use_redis,
                "default_ttl": self.config.default_ttl,
                "memory_max_size": self.config.memory_max_size,
            },
            "stats": {
                **self._stats,
                "hit_rate": round(hit_rate, 2),
                "memory_items": len(self._memory_cache),
                "weak_refs": len(self._weak_refs),
            },
            "redis_available": self._redis_client is not None,
        }


class CacheManager:
    """全局缓存管理器"""

    def __init__(self):
        self._caches = {}
        self._default_config = CacheConfig()

    def get_cache(self, name, config=None):
        if name not in self._caches:
            if config is None:
                config = self._default_config
            self._caches[name] = UnifiedCache(config, name)
        return self._caches[name]

    def create_cache(self, name, **config_kwargs):
        config = CacheConfig(**config_kwargs)
        cache = UnifiedCache(config, name)
        self._caches[name] = cache
        return cache

    async def clear_all(self):
        for cache in self._caches.values():
            await cache.clear()

    async def get_all_stats(self):
        stats = {}
        for name, cache in self._caches.items():
            stats[name] = await cache.get_stats()
        return stats

    def list_caches(self):
        return list(self._caches.keys())


cache_manager = CacheManager()


def get_cache(name="default", **config_kwargs):
    if config_kwargs:
        config = CacheConfig(**config_kwargs)
        return cache_manager.get_cache(name, config)
    return cache_manager.get_cache(name)


unified_cache = get_cache(
    "unified",
    use_redis=getattr(settings, "CACHE_USE_REDIS", False),
    default_ttl=getattr(settings, "CACHE_DEFAULT_TTL", 300),
    redis_key_prefix="cache:",
)

user_cache = unified_cache
metrics_cache = unified_cache
api_cache = unified_cache
query_cache = unified_cache
