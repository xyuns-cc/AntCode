"""缓存基础设施模块"""

from antcode_core.infrastructure.cache.cache import (
    CacheConfig,
    CacheManager,
    UnifiedCache,
    api_cache,
    cache_manager,
    get_cache,
    metrics_cache,
    query_cache,
    unified_cache,
    user_cache,
)
__all__ = [
    # 缓存核心
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
]
