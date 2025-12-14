"""
熔断器实现

基于状态机的熔断器模式，防止级联故障：
- CLOSED: 正常状态，请求正常通过
- OPEN: 熔断状态，快速失败
- HALF_OPEN: 半开状态，允许探测请求

使用示例:
    # 装饰器方式
    @circuit_breaker(name="redis", failure_threshold=5)
    async def call_redis():
        ...

    # 上下文管理器方式
    async with CircuitBreaker("external_api") as cb:
        result = await external_api.call()
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Optional,
    Set,
    TypeVar,
    Union,
)

from loguru import logger


class CircuitState(str, Enum):
    """熔断器状态"""
    CLOSED = "closed"       # 正常，请求通过
    OPEN = "open"           # 熔断，快速失败
    HALF_OPEN = "half_open" # 半开，允许探测


class CircuitBreakerError(Exception):
    """熔断器异常基类"""
    pass


class CircuitOpenError(CircuitBreakerError):
    """熔断器打开异常"""
    def __init__(self, name: str, remaining_seconds: float):
        self.name = name
        self.remaining_seconds = remaining_seconds
        super().__init__(
            f"熔断器 '{name}' 已打开，剩余 {remaining_seconds:.1f} 秒恢复"
        )


@dataclass
class CircuitBreakerConfig:
    """熔断器配置"""
    # 失败阈值：连续失败次数达到此值触发熔断
    failure_threshold: int = 5
    # 成功阈值：半开状态下连续成功次数达到此值恢复正常
    success_threshold: int = 2
    # 熔断持续时间（秒）
    timeout: float = 30.0
    # 半开状态允许的最大并发探测请求数
    half_open_max_calls: int = 3
    # 需要触发熔断的异常类型（空则所有异常都触发）
    exception_types: Set[type] = field(default_factory=set)
    # 排除的异常类型（这些异常不触发熔断）
    excluded_exceptions: Set[type] = field(default_factory=set)


@dataclass
class CircuitBreakerStats:
    """熔断器统计信息"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    state_changes: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreaker:
    """
    熔断器实现
    
    状态转换：
    CLOSED -> OPEN: 连续失败达到阈值
    OPEN -> HALF_OPEN: 超时后自动转换
    HALF_OPEN -> CLOSED: 连续成功达到阈值
    HALF_OPEN -> OPEN: 任意失败
    """
    
    # 全局熔断器注册表
    _instances: Dict[str, "CircuitBreaker"] = {}
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        """
        初始化熔断器
        
        Args:
            name: 熔断器名称（用于标识和日志）
            config: 熔断器配置
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._open_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
        
        # 注册到全局
        CircuitBreaker._instances[name] = self
    
    @classmethod
    def get(cls, name: str) -> Optional["CircuitBreaker"]:
        """获取已注册的熔断器"""
        return cls._instances.get(name)
    
    @classmethod
    def get_all(cls) -> Dict[str, "CircuitBreaker"]:
        """获取所有熔断器"""
        return cls._instances.copy()
    
    @property
    def state(self) -> CircuitState:
        """当前状态"""
        return self._state
    
    @property
    def stats(self) -> CircuitBreakerStats:
        """统计信息"""
        return self._stats
    
    @property
    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        return self._state == CircuitState.HALF_OPEN
    
    def _should_trip(self, exception: Exception) -> bool:
        """判断异常是否应该触发熔断"""
        exc_type = type(exception)
        
        # 排除的异常不触发
        if exc_type in self.config.excluded_exceptions:
            return False
        
        # 如果指定了异常类型，只有匹配的才触发
        if self.config.exception_types:
            return any(
                isinstance(exception, t) 
                for t in self.config.exception_types
            )
        
        # 默认所有异常都触发
        return True
    
    async def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换"""
        if self._state == new_state:
            return
        
        old_state = self._state
        self._state = new_state
        self._stats.state_changes += 1
        
        if new_state == CircuitState.OPEN:
            self._open_time = time.time()
            self._half_open_calls = 0
            logger.warning(
                f"熔断器 '{self.name}' 已打开 "
                f"(连续失败: {self._stats.consecutive_failures})"
            )
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            logger.info(f"熔断器 '{self.name}' 进入半开状态")
        elif new_state == CircuitState.CLOSED:
            self._stats.consecutive_failures = 0
            logger.info(f"熔断器 '{self.name}' 已恢复正常")
    
    async def _check_state(self) -> bool:
        """
        检查并更新状态
        
        Returns:
            是否允许请求通过
        """
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            
            if self._state == CircuitState.OPEN:
                # 检查是否超时
                elapsed = time.time() - (self._open_time or 0)
                if elapsed >= self.config.timeout:
                    await self._transition_to(CircuitState.HALF_OPEN)
                    return True
                return False
            
            if self._state == CircuitState.HALF_OPEN:
                # 限制半开状态的并发请求
                if self._half_open_calls < self.config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
        
        return False
    
    async def _record_success(self) -> None:
        """记录成功"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.successful_calls += 1
            self._stats.last_success_time = time.time()
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes += 1
            
            if self._state == CircuitState.HALF_OPEN:
                if self._stats.consecutive_successes >= self.config.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)
    
    async def _record_failure(self, exception: Exception) -> None:
        """记录失败"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.failed_calls += 1
            self._stats.last_failure_time = time.time()
            self._stats.consecutive_successes = 0
            
            if not self._should_trip(exception):
                return
            
            self._stats.consecutive_failures += 1
            
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态下任意失败都重新打开
                await self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                if self._stats.consecutive_failures >= self.config.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
    
    async def call(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        通过熔断器执行调用
        
        Args:
            func: 要执行的函数
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            函数返回值
            
        Raises:
            CircuitOpenError: 熔断器打开时
        """
        if not await self._check_state():
            self._stats.rejected_calls += 1
            remaining = self.config.timeout - (time.time() - (self._open_time or 0))
            raise CircuitOpenError(self.name, max(0, remaining))
        
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            await self._record_success()
            return result
            
        except Exception as e:
            await self._record_failure(e)
            raise
    
    async def __aenter__(self) -> "CircuitBreaker":
        """异步上下文管理器入口"""
        if not await self._check_state():
            self._stats.rejected_calls += 1
            remaining = self.config.timeout - (time.time() - (self._open_time or 0))
            raise CircuitOpenError(self.name, max(0, remaining))
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """异步上下文管理器出口"""
        if exc_val is None:
            await self._record_success()
        else:
            await self._record_failure(exc_val)
        return False  # 不抑制异常
    
    def reset(self) -> None:
        """重置熔断器状态"""
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._open_time = None
        self._half_open_calls = 0
        logger.info(f"熔断器 '{self.name}' 已重置")
    
    def get_status(self) -> Dict[str, Any]:
        """获取熔断器状态"""
        remaining_timeout = 0.0
        if self._state == CircuitState.OPEN and self._open_time:
            remaining_timeout = max(
                0, 
                self.config.timeout - (time.time() - self._open_time)
            )
        
        return {
            "name": self.name,
            "state": self._state.value,
            "stats": {
                "total_calls": self._stats.total_calls,
                "successful_calls": self._stats.successful_calls,
                "failed_calls": self._stats.failed_calls,
                "rejected_calls": self._stats.rejected_calls,
                "state_changes": self._stats.state_changes,
                "consecutive_failures": self._stats.consecutive_failures,
                "consecutive_successes": self._stats.consecutive_successes,
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout,
            },
            "remaining_timeout": round(remaining_timeout, 1),
        }


T = TypeVar("T")


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    success_threshold: int = 2,
    timeout: float = 30.0,
    exception_types: Optional[Set[type]] = None,
    excluded_exceptions: Optional[Set[type]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    熔断器装饰器
    
    Args:
        name: 熔断器名称
        failure_threshold: 失败阈值
        success_threshold: 成功阈值
        timeout: 熔断超时时间
        exception_types: 触发熔断的异常类型
        excluded_exceptions: 排除的异常类型
        
    Returns:
        装饰器函数
        
    Example:
        @circuit_breaker(name="redis", failure_threshold=5)
        async def get_from_redis(key: str) -> str:
            return await redis.get(key)
    """
    config = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        success_threshold=success_threshold,
        timeout=timeout,
        exception_types=exception_types or set(),
        excluded_exceptions=excluded_exceptions or set(),
    )
    
    cb = CircuitBreaker.get(name)
    if cb is None:
        cb = CircuitBreaker(name, config)
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            return await cb.call(func, *args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            return asyncio.get_event_loop().run_until_complete(
                cb.call(func, *args, **kwargs)
            )
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
