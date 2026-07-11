"""Redis-only cache infrastructure."""

import gzip
from dataclasses import dataclass

from loguru import logger
from redis.asyncio import Redis

from antcode_core.common.config import settings
from antcode_core.common.serialization import from_json, to_json


@dataclass(frozen=True)
class CacheConfig:
    """Redis cache configuration."""

    default_ttl: int = 300
    max_ttl: int = 3600
    min_ttl: int = 10
    redis_key_prefix: str = "cache:"
    redis_connection_timeout: int = 5
    enable_compression: bool = False
    compression_threshold: int = 1024

    @classmethod
    def from_settings(cls, prefix: str = "") -> "CacheConfig":
        key_prefix = f"cache:{prefix.lower().rstrip('_')}:" if prefix else "cache:"
        return cls(default_ttl=settings.CACHE_DEFAULT_TTL, redis_key_prefix=key_prefix)


class UnifiedCache:
    """Redis cache manager."""

    def __init__(self, config: CacheConfig, name: str = "default"):
        self.config = config
        self.name = name
        self._redis_client: Redis | None = None
        self._stats = {"hits": 0, "misses": 0, "sets": 0, "deletes": 0, "errors": 0}
        logger.info(f"Redis 缓存 '{name}' 已初始化: TTL={config.default_ttl}s")

    async def _get_redis_client(self):
        if self._redis_client is None:
            from antcode_core.infrastructure.redis import get_redis_client

            self._redis_client = await get_redis_client()
            logger.info(f"Redis 缓存 '{self.name}' 已连接")
        return self._redis_client

    def _generate_key(self, key: str) -> str:
        return f"{self.config.redis_key_prefix}{key}"

    def _serialize_value(self, value) -> bytes:
        data = to_json(value).encode("utf-8")
        if self.config.enable_compression and len(data) > self.config.compression_threshold:
            return gzip.compress(data)
        return data

    def _deserialize_value(self, data: bytes):
        raw = self._maybe_decompress(data)
        return from_json(raw.decode("utf-8"))

    def _maybe_decompress(self, data: bytes) -> bytes:
        if not self.config.enable_compression:
            return data
        try:
            return gzip.decompress(data)
        except gzip.BadGzipFile:
            return data

    def _normalize_ttl(self, ttl: int | None) -> int:
        selected = self.config.default_ttl if ttl is None else ttl
        return max(self.config.min_ttl, min(selected, self.config.max_ttl))

    async def get(self, key: str):
        try:
            data = await (await self._get_redis_client()).get(self._generate_key(key))
            if data is None:
                self._stats["misses"] += 1
                return None
            self._stats["hits"] += 1
            return self._deserialize_value(data)
        except Exception as exc:
            self._stats["errors"] += 1
            logger.error(f"Redis 缓存读取失败: {exc}")
            raise

    async def set(self, key: str, value, ttl: int | None = None) -> bool:
        try:
            payload = self._serialize_value(value)
            await (await self._get_redis_client()).setex(
                self._generate_key(key),
                self._normalize_ttl(ttl),
                payload,
            )
            self._stats["sets"] += 1
            return True
        except Exception as exc:
            self._stats["errors"] += 1
            logger.error(f"Redis 缓存写入失败: {exc}")
            raise

    async def delete(self, key: str) -> bool:
        try:
            result = await (await self._get_redis_client()).delete(self._generate_key(key))
            self._stats["deletes"] += 1
            return result > 0
        except Exception as exc:
            self._stats["errors"] += 1
            logger.error(f"Redis 缓存删除失败: {exc}")
            raise

    async def clear(self) -> bool:
        await self.clear_prefix("")
        return True

    async def clear_prefix(self, key_prefix: str) -> bool:
        client = await self._get_redis_client()
        pattern = f"{self.config.redis_key_prefix}{key_prefix}*"
        deleted = await self._delete_pattern(client, pattern)
        if deleted:
            self._stats["deletes"] += deleted
        logger.info(f"Redis 缓存 '{self.name}' 前缀已清除: {key_prefix}")
        return True

    async def _delete_pattern(self, client, pattern: str) -> int:
        deleted = 0
        batch: list[bytes | str] = []
        async for key in client.scan_iter(match=pattern, count=500):
            batch.append(key)
            if len(batch) >= 500:
                deleted += await client.delete(*batch)
                batch.clear()
        if batch:
            deleted += await client.delete(*batch)
        return deleted

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def get_stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = round(self._stats["hits"] / total * 100, 2) if total else 0
        return {
            "name": self.name,
            "config": {
                "backend": "redis",
                "default_ttl": self.config.default_ttl,
            },
            "stats": {**self._stats, "hit_rate": hit_rate},
            "redis_available": self._redis_client is not None,
        }


class CacheManager:
    """Global Redis cache registry."""

    def __init__(self):
        self._caches: dict[str, UnifiedCache] = {}
        self._default_config = CacheConfig()

    def get_cache(self, name: str, config: CacheConfig | None = None) -> UnifiedCache:
        if name not in self._caches:
            self._caches[name] = UnifiedCache(config or self._default_config, name)
        return self._caches[name]

    def create_cache(self, name: str, **config_kwargs) -> UnifiedCache:
        cache = UnifiedCache(CacheConfig(**config_kwargs), name)
        self._caches[name] = cache
        return cache

    async def clear_all(self) -> None:
        for cache in self._caches.values():
            await cache.clear()

    async def get_all_stats(self) -> dict:
        return {name: await cache.get_stats() for name, cache in self._caches.items()}

    def list_caches(self) -> list[str]:
        return list(self._caches.keys())


cache_manager = CacheManager()


def get_cache(name: str = "default", **config_kwargs) -> UnifiedCache:
    if config_kwargs:
        return cache_manager.get_cache(name, CacheConfig(**config_kwargs))
    return cache_manager.get_cache(name)


unified_cache = get_cache("unified", default_ttl=settings.CACHE_DEFAULT_TTL)
user_cache = unified_cache
metrics_cache = unified_cache
api_cache = unified_cache
query_cache = unified_cache
