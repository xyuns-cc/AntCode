"""
服务降级策略

提供多种降级策略：
- 缓存降级：Redis 不可用时降级到内存缓存
- 默认值降级：服务不可用时返回默认值
- 备用服务降级：主服务不可用时切换到备用服务

使用示例:
    # 装饰器方式
    @fallback(CacheFallback(default_value={}))
    async def get_user_data(user_id: int) -> dict:
        return await redis.hgetall(f"user:{user_id}")

    # 手动方式
    fallback_strategy = CacheFallback(default_value={})
    try:
        result = await redis.get(key)
    except Exception as e:
        result = await fallback_strategy.execute(e, key)
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import wraps
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Optional,
    TypeVar,
    Union,
)

from loguru import logger


T = TypeVar("T")


class FallbackStrategy(ABC, Generic[T]):
    """降级策略抽象基类"""
    
    @abstractmethod
    async def execute(
        self,
        exception: Exception,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        执行降级逻辑
        
        Args:
            exception: 触发降级的异常
            *args: 原始调用的位置参数
            **kwargs: 原始调用的关键字参数
            
        Returns:
            降级后的返回值
        """
        pass
    
    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """获取降级策略状态"""
        pass


@dataclass
class FallbackStats:
    """降级统计信息"""
    total_fallbacks: int = 0
    successful_fallbacks: int = 0
    failed_fallbacks: int = 0
    last_fallback_time: Optional[float] = None
    fallback_reasons: Dict[str, int] = field(default_factory=dict)


class DefaultValueFallback(FallbackStrategy[T]):
    """
    默认值降级策略
    
    当服务不可用时返回预设的默认值。
    """
    
    def __init__(
        self,
        default_value: T,
        log_fallback: bool = True,
    ):
        """
        初始化默认值降级策略
        
        Args:
            default_value: 降级时返回的默认值
            log_fallback: 是否记录降级日志
        """
        self.default_value = default_value
        self.log_fallback = log_fallback
        self._stats = FallbackStats()
    
    async def execute(
        self,
        exception: Exception,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """执行降级，返回默认值"""
        self._stats.total_fallbacks += 1
        self._stats.last_fallback_time = time.time()
        
        reason = type(exception).__name__
        self._stats.fallback_reasons[reason] = (
            self._stats.fallback_reasons.get(reason, 0) + 1
        )
        
        if self.log_fallback:
            logger.warning(f"服务降级，返回默认值: {exception}")
        
        self._stats.successful_fallbacks += 1
        return self.default_value
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy": "default_value",
            "default_value_type": type(self.default_value).__name__,
            "stats": {
                "total_fallbacks": self._stats.total_fallbacks,
                "successful_fallbacks": self._stats.successful_fallbacks,
                "failed_fallbacks": self._stats.failed_fallbacks,
                "fallback_reasons": self._stats.fallback_reasons,
            },
        }


class CacheFallback(FallbackStrategy[T]):
    """
    缓存降级策略
    
    当主缓存（如 Redis）不可用时，降级到内存缓存。
    支持 TTL 和最大容量限制。
    """
    
    def __init__(
        self,
        default_value: T,
        max_size: int = 1000,
        ttl: float = 300.0,
        log_fallback: bool = True,
    ):
        """
        初始化缓存降级策略
        
        Args:
            default_value: 缓存未命中时的默认值
            max_size: 内存缓存最大条目数
            ttl: 缓存 TTL（秒）
            log_fallback: 是否记录降级日志
        """
        self.default_value = default_value
        self.max_size = max_size
        self.ttl = ttl
        self.log_fallback = log_fallback
        
        self._cache: Dict[str, tuple[T, float]] = {}
        self._stats = FallbackStats()
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = asyncio.Lock()
    
    def _make_key(self, *args: Any, **kwargs: Any) -> str:
        """生成缓存键"""
        key_parts = [str(arg) for arg in args]
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)
    
    async def _cleanup_expired(self) -> None:
        """清理过期缓存"""
        now = time.time()
        expired_keys = [
            key for key, (_, expire_time) in self._cache.items()
            if expire_time < now
        ]
        for key in expired_keys:
            del self._cache[key]
    
    async def _evict_oldest(self) -> None:
        """驱逐最旧的缓存条目"""
        if len(self._cache) >= self.max_size:
            # 按过期时间排序，删除最早过期的
            oldest_key = min(
                self._cache.keys(),
                key=lambda k: self._cache[k][1]
            )
            del self._cache[oldest_key]
    
    async def get(self, key: str) -> Optional[T]:
        """从内存缓存获取值"""
        async with self._lock:
            if key in self._cache:
                value, expire_time = self._cache[key]
                if expire_time > time.time():
                    self._cache_hits += 1
                    return value
                else:
                    del self._cache[key]
            self._cache_misses += 1
            return None
    
    async def set(self, key: str, value: T) -> None:
        """设置内存缓存值"""
        async with self._lock:
            await self._cleanup_expired()
            await self._evict_oldest()
            self._cache[key] = (value, time.time() + self.ttl)
    
    async def execute(
        self,
        exception: Exception,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        执行降级逻辑
        
        1. 尝试从内存缓存获取
        2. 缓存未命中则返回默认值
        """
        self._stats.total_fallbacks += 1
        self._stats.last_fallback_time = time.time()
        
        reason = type(exception).__name__
        self._stats.fallback_reasons[reason] = (
            self._stats.fallback_reasons.get(reason, 0) + 1
        )
        
        if self.log_fallback:
            logger.warning(f"Redis 不可用，降级到内存缓存: {exception}")
        
        # 尝试从内存缓存获取
        cache_key = self._make_key(*args, **kwargs)
        cached_value = await self.get(cache_key)
        
        if cached_value is not None:
            self._stats.successful_fallbacks += 1
            return cached_value
        
        # 缓存未命中，返回默认值
        self._stats.successful_fallbacks += 1
        return self.default_value
    
    async def cache_result(
        self,
        result: T,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """缓存成功的结果（用于正常情况下的缓存）"""
        cache_key = self._make_key(*args, **kwargs)
        await self.set(cache_key, result)
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy": "cache_fallback",
            "cache_size": len(self._cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": (
                round(self._cache_hits / (self._cache_hits + self._cache_misses) * 100, 2)
                if (self._cache_hits + self._cache_misses) > 0 else 0
            ),
            "stats": {
                "total_fallbacks": self._stats.total_fallbacks,
                "successful_fallbacks": self._stats.successful_fallbacks,
                "failed_fallbacks": self._stats.failed_fallbacks,
                "fallback_reasons": self._stats.fallback_reasons,
            },
        }
    
    async def clear(self) -> None:
        """清空内存缓存"""
        async with self._lock:
            self._cache.clear()


class CallbackFallback(FallbackStrategy[T]):
    """
    回调函数降级策略
    
    当服务不可用时，调用自定义的降级函数。
    """
    
    def __init__(
        self,
        fallback_func: Callable[..., T],
        log_fallback: bool = True,
    ):
        """
        初始化回调降级策略
        
        Args:
            fallback_func: 降级时调用的函数
            log_fallback: 是否记录降级日志
        """
        self.fallback_func = fallback_func
        self.log_fallback = log_fallback
        self._stats = FallbackStats()
    
    async def execute(
        self,
        exception: Exception,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """执行降级回调"""
        self._stats.total_fallbacks += 1
        self._stats.last_fallback_time = time.time()
        
        reason = type(exception).__name__
        self._stats.fallback_reasons[reason] = (
            self._stats.fallback_reasons.get(reason, 0) + 1
        )
        
        if self.log_fallback:
            logger.warning(f"服务降级，执行回调函数: {exception}")
        
        try:
            if asyncio.iscoroutinefunction(self.fallback_func):
                result = await self.fallback_func(*args, **kwargs)
            else:
                result = self.fallback_func(*args, **kwargs)
            
            self._stats.successful_fallbacks += 1
            return result
            
        except Exception as e:
            self._stats.failed_fallbacks += 1
            logger.error(f"降级回调执行失败: {e}")
            raise
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy": "callback_fallback",
            "fallback_func": self.fallback_func.__name__,
            "stats": {
                "total_fallbacks": self._stats.total_fallbacks,
                "successful_fallbacks": self._stats.successful_fallbacks,
                "failed_fallbacks": self._stats.failed_fallbacks,
                "fallback_reasons": self._stats.fallback_reasons,
            },
        }


def fallback(
    strategy: FallbackStrategy[T],
    exception_types: Optional[tuple[type, ...]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    降级装饰器
    
    Args:
        strategy: 降级策略
        exception_types: 触发降级的异常类型（默认所有异常）
        
    Returns:
        装饰器函数
        
    Example:
        @fallback(DefaultValueFallback(default_value=[]))
        async def get_user_list() -> list:
            return await external_service.get_users()
    """
    exception_types = exception_types or (Exception,)
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                result = await func(*args, **kwargs)
                
                # 如果是缓存降级策略，缓存成功结果
                if isinstance(strategy, CacheFallback):
                    await strategy.cache_result(result, *args, **kwargs)
                
                return result
                
            except exception_types as e:
                return await strategy.execute(e, *args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                result = func(*args, **kwargs)
                return result
            except exception_types as e:
                return asyncio.get_event_loop().run_until_complete(
                    strategy.execute(e, *args, **kwargs)
                )
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
