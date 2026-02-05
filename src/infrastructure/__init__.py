"""基础设施层

提供缓存、Redis、中间件等底层基础设施实现。
"""

from src.infrastructure.cache import (
    CacheConfig,
    UnifiedCache,
    CacheManager,
    cache_manager,
    get_cache,
    unified_cache,
    user_cache,
    metrics_cache,
    api_cache,
    query_cache,
)
from src.infrastructure.redis import (
    RedisConnectionPool,
    get_redis_client,
    close_redis_pool,
)
from src.infrastructure.middleware import (
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
    CacheInvalidationMiddleware,
)

__all__ = [
    # Cache
    "CacheConfig",
    "UnifiedCache",
    "CacheManager",
    "cache_manager",
    "get_cache",
    "unified_cache",
    "user_cache",
    "metrics_cache",
    "api_cache",
    "query_cache",
    # Redis
    "RedisConnectionPool",
    "get_redis_client",
    "close_redis_pool",
    # Middleware
    "SecurityHeadersMiddleware",
    "RateLimitMiddleware",
    "CacheInvalidationMiddleware",
]
