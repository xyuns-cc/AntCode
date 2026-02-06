"""
弹性机制

统一的断路器、限流器和退避重试实现。

Requirements: 18.5
"""

import asyncio
import contextlib
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from loguru import logger

from antcode_worker.domain.events import CircuitBreakerStateChanged, event_bus

# ==================== 指数退避 ====================


@dataclass
class BackoffConfig:
    """退避配置"""
    base_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: float = 0.1
    max_retries: int = -1  # -1 表示无限


class ExponentialBackoff:
    """指数退避"""

    def __init__(self, config: BackoffConfig | None = None):
        self._config = config or BackoffConfig()
        self._current_delay = self._config.base_delay
        self._retry_count = 0

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def current_delay(self) -> float:
        return self._current_delay

    def reset(self) -> None:
        self._current_delay = self._config.base_delay
        self._retry_count = 0

    def should_retry(self) -> bool:
        if self._config.max_retries < 0:
            return True
        return self._retry_count < self._config.max_retries

    async def wait(self) -> float:
        """等待并返回实际等待时间"""
        jitter = 1.0 + (random.random() * 2 - 1) * self._config.jitter
        delay = self._current_delay * jitter

        await asyncio.sleep(delay)

        self._retry_count += 1
        self._current_delay = min(
            self._current_delay * self._config.multiplier,
            self._config.max_delay,
        )
        return delay

    def get_delay_for_attempt(self, attempt: int) -> float:
        """获取指定尝试次数的延迟（不改变状态）"""
        delay = self._config.base_delay * (self._config.multiplier ** attempt)
        return min(delay, self._config.max_delay)


# ==================== 断路器 ====================


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """断路器配置"""
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1
    success_threshold: int = 1


class CircuitOpenError(Exception):
    """断路器打开异常"""
    def __init__(self, message: str = "Circuit breaker is open", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitBreaker:
    """断路器"""

    def __init__(self, name: str = "default", config: CircuitBreakerConfig | None = None):
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: datetime | None = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

        # 统计
        self._total_calls = 0
        self._rejected_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    async def call(self, func, *args, **kwargs):
        """通过断路器调用"""
        await self._check_and_acquire()
        self._total_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise

    async def _check_and_acquire(self) -> None:
        async with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.OPEN:
                self._rejected_calls += 1
                retry_after = self._calculate_retry_after()
                raise CircuitOpenError(f"Circuit '{self._name}' is open", retry_after)

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self._config.half_open_max_calls:
                    self._rejected_calls += 1
                    raise CircuitOpenError(f"Circuit '{self._name}' half-open limit reached")
                self._half_open_calls += 1

    def _check_state_transition(self) -> None:
        if self._state == CircuitState.OPEN and self._last_failure_time:
            elapsed = (datetime.now() - self._last_failure_time).total_seconds()
            if elapsed >= self._config.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0
                logger.info(f"Circuit '{self._name}': OPEN -> HALF_OPEN")

    def _calculate_retry_after(self) -> float | None:
        if self._last_failure_time:
            elapsed = (datetime.now() - self._last_failure_time).total_seconds()
            remaining = self._config.recovery_timeout - elapsed
            return max(0, remaining)
        return None

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now()

            if self._state == CircuitState.HALF_OPEN:
                # HALF_OPEN 状态下任何失败都应该触发 OPEN
                await self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED and self._failure_count >= self._config.failure_threshold:
                # CLOSED 状态下达到失败阈值才触发 OPEN
                await self._transition_to(CircuitState.OPEN)

    async def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state

        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._half_open_calls = 0
            self._success_count = 0
            logger.info(f"Circuit '{self._name}': {old_state.value} -> CLOSED")
        elif new_state == CircuitState.OPEN:
            self._half_open_calls = 0
            self._success_count = 0
            logger.warning(f"Circuit '{self._name}': {old_state.value} -> OPEN (failures={self._failure_count})")

        await event_bus.publish(CircuitBreakerStateChanged(
            circuit_name=self._name,
            old_state=old_state.value,
            new_state=new_state.value,
            failure_count=self._failure_count,
        ))

    async def reset(self) -> None:
        async with self._lock:
            if self._state != CircuitState.CLOSED:
                await self._transition_to(CircuitState.CLOSED)

    def get_stats(self) -> dict:
        return {
            "name": self._name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "total_calls": self._total_calls,
            "rejected_calls": self._rejected_calls,
        }


# ==================== 限流器 ====================


class RateLimiterState(str, Enum):
    NORMAL = "normal"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"


@dataclass
class RateLimiterConfig:
    """限流器配置"""
    max_rate: float = 100.0
    cpu_threshold: float = 90.0
    memory_threshold: float = 85.0
    window_size: float = 1.0
    recovery_interval: float = 5.0


@dataclass
class AcquireResult:
    """获取许可结果"""
    allowed: bool
    retry_after: float | None = None
    reason: str | None = None


class RateLimiter:
    """限流器"""

    def __init__(self, name: str = "default", config: RateLimiterConfig | None = None):
        self._name = name
        self._config = config or RateLimiterConfig()
        self._request_times: list[float] = []
        self._lock = asyncio.Lock()
        self._state = RateLimiterState.NORMAL
        self._pause_reason: str | None = None
        self._recovery_task: asyncio.Task | None = None
        self._running = False

        # 统计
        self._total_requests = 0
        self._rejected_requests = 0

    @property
    def state(self) -> RateLimiterState:
        return self._state

    @property
    def is_paused(self) -> bool:
        return self._state == RateLimiterState.OVERLOADED

    async def acquire(self) -> AcquireResult:
        """尝试获取许可"""
        self._total_requests += 1

        # 检查系统负载
        overload = await self._check_overload()
        if overload:
            self._rejected_requests += 1
            return overload

        # 检查速率
        async with self._lock:
            now = datetime.now().timestamp()
            cutoff = now - self._config.window_size
            self._request_times = [t for t in self._request_times if t > cutoff]

            if len(self._request_times) >= self._config.max_rate:
                self._rejected_requests += 1
                retry_after = self._config.window_size - (now - self._request_times[0]) if self._request_times else 0.1
                return AcquireResult(False, max(0.1, retry_after), "Rate limit exceeded")

            self._request_times.append(now)
            return AcquireResult(True)

    async def _check_overload(self) -> AcquireResult | None:
        """检查系统负载"""
        try:
            # 尝试导入系统指标模块
            from antcode_worker.heartbeat.system_metrics import get_system_metrics
            metrics = get_system_metrics()

            if metrics.cpu_percent > self._config.cpu_threshold:
                self._pause_reason = f"CPU overload: {metrics.cpu_percent:.1f}%"
                if self._state != RateLimiterState.OVERLOADED:
                    self._state = RateLimiterState.OVERLOADED
                    self._start_recovery()
                return AcquireResult(False, 5.0, self._pause_reason)

            if metrics.memory_percent > self._config.memory_threshold:
                self._pause_reason = f"Memory overload: {metrics.memory_percent:.1f}%"
                if self._state != RateLimiterState.OVERLOADED:
                    self._state = RateLimiterState.OVERLOADED
                    self._start_recovery()
                return AcquireResult(False, 5.0, self._pause_reason)

            if self._state == RateLimiterState.OVERLOADED:
                self._state = RateLimiterState.NORMAL
                self._pause_reason = None
        except ImportError:
            # 如果无法导入系统指标模块，跳过负载检查
            pass

        return None

    def _start_recovery(self) -> None:
        if self._recovery_task is None or self._recovery_task.done():
            self._running = True
            self._recovery_task = asyncio.create_task(self._recovery_loop())

    async def _recovery_loop(self) -> None:
        while self._running and self._state == RateLimiterState.OVERLOADED:
            try:
                await asyncio.sleep(self._config.recovery_interval)

                try:
                    from antcode_worker.heartbeat.system_metrics import get_system_metrics
                    metrics = get_system_metrics()

                    cpu_ok = metrics.cpu_percent < (self._config.cpu_threshold - 5)
                    mem_ok = metrics.memory_percent < (self._config.memory_threshold - 5)

                    if cpu_ok and mem_ok:
                        self._state = RateLimiterState.NORMAL
                        self._pause_reason = None
                        logger.info(f"RateLimiter '{self._name}': recovered")
                        break
                except ImportError:
                    # 如果无法导入，直接恢复
                    self._state = RateLimiterState.NORMAL
                    self._pause_reason = None
                    break
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        self._running = False
        if self._recovery_task:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task

    def get_stats(self) -> dict:
        return {
            "name": self._name,
            "state": self._state.value,
            "total_requests": self._total_requests,
            "rejected_requests": self._rejected_requests,
            "pause_reason": self._pause_reason,
        }
