"""
弹性机制测试

测试断路器、限流器和指数退避的功能。
"""

import asyncio
import sys
from pathlib import Path

import pytest

# 添加 worker 源码路径
worker_src = Path(__file__).parent.parent.parent.parent / "services" / "worker" / "src"
if str(worker_src) not in sys.path:
    sys.path.insert(0, str(worker_src))

from antcode_worker.services.resilience import (
    BackoffConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    ExponentialBackoff,
    RateLimiter,
    RateLimiterConfig,
)


class TestExponentialBackoff:
    """指数退避测试"""

    def test_initial_delay(self):
        """测试初始延迟"""
        config = BackoffConfig(base_delay=1.0, max_delay=60.0)
        backoff = ExponentialBackoff(config)
        assert backoff.current_delay == 1.0
        assert backoff.retry_count == 0

    def test_should_retry_unlimited(self):
        """测试无限重试"""
        config = BackoffConfig(max_retries=-1)
        backoff = ExponentialBackoff(config)
        for _ in range(100):
            assert backoff.should_retry()
            backoff._retry_count += 1

    def test_should_retry_limited(self):
        """测试有限重试"""
        config = BackoffConfig(max_retries=3)
        backoff = ExponentialBackoff(config)

        for i in range(3):
            assert backoff.should_retry()
            backoff._retry_count += 1

        assert not backoff.should_retry()

    def test_reset(self):
        """测试重置"""
        config = BackoffConfig(base_delay=1.0)
        backoff = ExponentialBackoff(config)
        backoff._retry_count = 5
        backoff._current_delay = 30.0

        backoff.reset()

        assert backoff.retry_count == 0
        assert backoff.current_delay == 1.0

    def test_get_delay_for_attempt(self):
        """测试获取指定尝试的延迟"""
        config = BackoffConfig(base_delay=1.0, multiplier=2.0, max_delay=60.0)
        backoff = ExponentialBackoff(config)

        assert backoff.get_delay_for_attempt(0) == 1.0
        assert backoff.get_delay_for_attempt(1) == 2.0
        assert backoff.get_delay_for_attempt(2) == 4.0
        assert backoff.get_delay_for_attempt(10) == 60.0  # 受 max_delay 限制


class TestCircuitBreaker:
    """断路器测试"""

    @pytest.mark.asyncio
    async def test_initial_state(self):
        """测试初始状态"""
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED
        assert not cb.is_open

    @pytest.mark.asyncio
    async def test_success_keeps_closed(self):
        """测试成功调用保持关闭状态"""
        cb = CircuitBreaker("test")

        async def success_func():
            return "success"

        result = await cb.call(success_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failures_open_circuit(self):
        """测试失败次数达到阈值时打开断路器"""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker("test", config)

        async def fail_func():
            raise ValueError("test error")

        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(fail_func)

        assert cb.state == CircuitState.OPEN
        assert cb.is_open

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self):
        """测试打开的断路器拒绝调用"""
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60.0)
        cb = CircuitBreaker("test", config)

        async def fail_func():
            raise ValueError("test error")

        with pytest.raises(ValueError):
            await cb.call(fail_func)

        assert cb.state == CircuitState.OPEN

        async def success_func():
            return "success"

        with pytest.raises(CircuitOpenError):
            await cb.call(success_func)

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        """测试 HALF_OPEN 状态下失败会重新打开断路器（修复验证）"""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.01,
            half_open_max_calls=1,
        )
        cb = CircuitBreaker("test", config)

        async def fail_func():
            raise ValueError("test error")

        with pytest.raises(ValueError):
            await cb.call(fail_func)

        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.02)

        with pytest.raises(ValueError):
            await cb.call(fail_func)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_closes(self):
        """测试 HALF_OPEN 状态下成功会关闭断路器"""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.01,
            success_threshold=1,
        )
        cb = CircuitBreaker("test", config)

        async def fail_func():
            raise ValueError("test error")

        async def success_func():
            return "success"

        with pytest.raises(ValueError):
            await cb.call(fail_func)

        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.02)

        result = await cb.call(success_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reset(self):
        """测试手动重置"""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker("test", config)

        async def fail_func():
            raise ValueError("test error")

        with pytest.raises(ValueError):
            await cb.call(fail_func)

        assert cb.state == CircuitState.OPEN

        await cb.reset()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """测试统计信息"""
        cb = CircuitBreaker("test-stats")
        stats = cb.get_stats()

        assert stats["name"] == "test-stats"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["total_calls"] == 0


class TestRateLimiter:
    """限流器测试"""

    @pytest.mark.asyncio
    async def test_allows_within_limit(self):
        """测试在限制内允许请求"""
        config = RateLimiterConfig(max_rate=10, window_size=1.0)
        limiter = RateLimiter("test", config)

        for _ in range(5):
            result = await limiter.acquire()
            assert result.allowed

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self):
        """测试超过限制时拒绝请求"""
        config = RateLimiterConfig(max_rate=3, window_size=1.0)
        limiter = RateLimiter("test", config)

        for _ in range(3):
            result = await limiter.acquire()
            assert result.allowed

        result = await limiter.acquire()
        assert not result.allowed
        assert result.reason == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """测试统计信息"""
        limiter = RateLimiter("test-stats")

        await limiter.acquire()
        stats = limiter.get_stats()

        assert stats["name"] == "test-stats"
        assert stats["total_requests"] == 1
