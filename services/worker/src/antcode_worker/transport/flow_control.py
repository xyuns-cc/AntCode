"""
流量控制模块

实现 AIMD/token-bucket/window 限流和 backpressure 机制，
保护 Worker 和 Transport 层免受过载。

Requirements: 5.2
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class FlowControlStrategy(str, Enum):
    """流量控制策略"""
    TOKEN_BUCKET = "token_bucket"  # 令牌桶
    AIMD = "aimd"                  # 加性增乘性减
    SLIDING_WINDOW = "sliding_window"  # 滑动窗口


class BackpressureLevel(str, Enum):
    """背压级别"""
    NONE = "none"          # 无背压
    LOW = "low"            # 低背压（轻微减速）
    MEDIUM = "medium"      # 中等背压（明显减速）
    HIGH = "high"          # 高背压（严重减速）
    CRITICAL = "critical"  # 临界背压（暂停）


@dataclass
class FlowControlConfig:
    """流量控制配置"""
    # 通用配置
    strategy: FlowControlStrategy = FlowControlStrategy.TOKEN_BUCKET
    initial_rate: float = 100.0        # 初始速率（请求/秒）
    min_rate: float = 1.0              # 最小速率
    max_rate: float = 1000.0           # 最大速率

    # Token Bucket 配置
    bucket_capacity: int = 100         # 桶容量
    refill_rate: float = 10.0          # 填充速率（令牌/秒）

    # AIMD 配置
    additive_increase: float = 1.0     # 加性增量
    multiplicative_decrease: float = 0.5  # 乘性减量因子

    # Sliding Window 配置
    window_size: float = 1.0           # 窗口大小（秒）
    max_requests_per_window: int = 100  # 每窗口最大请求数

    # Backpressure 配置
    backpressure_threshold_low: float = 0.5     # 低背压阈值
    backpressure_threshold_medium: float = 0.7  # 中等背压阈值
    backpressure_threshold_high: float = 0.85   # 高背压阈值
    backpressure_threshold_critical: float = 0.95  # 临界背压阈值


@dataclass
class FlowControlStats:
    """流量控制统计"""
    total_requests: int = 0
    allowed_requests: int = 0
    rejected_requests: int = 0
    current_rate: float = 0.0
    backpressure_level: BackpressureLevel = BackpressureLevel.NONE
    last_update: float = field(default_factory=time.time)

    @property
    def rejection_rate(self) -> float:
        """拒绝率"""
        if self.total_requests == 0:
            return 0.0
        return self.rejected_requests / self.total_requests


class FlowController(ABC):
    """流量控制器抽象基类"""

    def __init__(self, config: FlowControlConfig | None = None):
        self._config = config or FlowControlConfig()
        self._stats = FlowControlStats()
        self._lock = asyncio.Lock()

    @abstractmethod
    async def acquire(self, count: int = 1, timeout: float | None = None) -> bool:
        """
        获取许可

        Args:
            count: 请求的许可数量
            timeout: 超时时间（秒），None 表示不等待

        Returns:
            是否获取成功
        """
        pass

    @abstractmethod
    async def release(self, count: int = 1) -> None:
        """
        释放许可（某些策略可能不需要）

        Args:
            count: 释放的许可数量
        """
        pass

    @abstractmethod
    def on_success(self) -> None:
        """请求成功回调（用于 AIMD 等自适应策略）"""
        pass

    @abstractmethod
    def on_failure(self) -> None:
        """请求失败回调（用于 AIMD 等自适应策略）"""
        pass

    @property
    def stats(self) -> FlowControlStats:
        """获取统计信息"""
        return self._stats

    @property
    def backpressure_level(self) -> BackpressureLevel:
        """获取当前背压级别"""
        return self._stats.backpressure_level

    def _update_backpressure(self, utilization: float) -> None:
        """根据利用率更新背压级别"""
        if utilization >= self._config.backpressure_threshold_critical:
            self._stats.backpressure_level = BackpressureLevel.CRITICAL
        elif utilization >= self._config.backpressure_threshold_high:
            self._stats.backpressure_level = BackpressureLevel.HIGH
        elif utilization >= self._config.backpressure_threshold_medium:
            self._stats.backpressure_level = BackpressureLevel.MEDIUM
        elif utilization >= self._config.backpressure_threshold_low:
            self._stats.backpressure_level = BackpressureLevel.LOW
        else:
            self._stats.backpressure_level = BackpressureLevel.NONE


class TokenBucketController(FlowController):
    """
    令牌桶流量控制器

    特点：
    - 允许突发流量（桶中有令牌时）
    - 平滑限流（令牌以固定速率填充）
    - 简单高效
    """

    def __init__(self, config: FlowControlConfig | None = None):
        super().__init__(config)
        self._tokens = float(self._config.bucket_capacity)
        self._last_refill = time.time()

    async def acquire(self, count: int = 1, timeout: float | None = None) -> bool:
        """获取令牌"""
        async with self._lock:
            self._stats.total_requests += 1

            # 填充令牌
            self._refill()

            # 检查是否有足够令牌
            if self._tokens >= count:
                self._tokens -= count
                self._stats.allowed_requests += 1
                self._update_backpressure(1 - self._tokens / self._config.bucket_capacity)
                return True

            # 如果没有超时，直接拒绝
            if timeout is None or timeout <= 0:
                self._stats.rejected_requests += 1
                return False

        # 等待令牌
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                async with self._lock:
                    self._stats.rejected_requests += 1
                return False

            # 计算需要等待的时间
            async with self._lock:
                self._refill()
                if self._tokens >= count:
                    self._tokens -= count
                    self._stats.allowed_requests += 1
                    self._update_backpressure(1 - self._tokens / self._config.bucket_capacity)
                    return True

                tokens_needed = count - self._tokens
                wait_time = min(tokens_needed / self._config.refill_rate, timeout - elapsed)

            await asyncio.sleep(wait_time)

    async def release(self, count: int = 1) -> None:
        """令牌桶不需要显式释放"""
        pass

    def on_success(self) -> None:
        """成功时可以略微增加填充速率"""
        pass

    def on_failure(self) -> None:
        """失败时可以略微降低填充速率"""
        pass

    def _refill(self) -> None:
        """填充令牌"""
        now = time.time()
        elapsed = now - self._last_refill
        self._last_refill = now

        # 计算新增令牌
        new_tokens = elapsed * self._config.refill_rate
        self._tokens = min(self._tokens + new_tokens, self._config.bucket_capacity)
        self._stats.current_rate = self._config.refill_rate


class AIMDController(FlowController):
    """
    AIMD (Additive Increase Multiplicative Decrease) 流量控制器

    特点：
    - 自适应调整速率
    - 成功时线性增加速率
    - 失败时乘性降低速率
    - 适合网络拥塞控制
    """

    def __init__(self, config: FlowControlConfig | None = None):
        super().__init__(config)
        self._current_rate = self._config.initial_rate
        self._window_start = time.time()
        self._requests_in_window = 0
        self._consecutive_successes = 0
        self._consecutive_failures = 0

    async def acquire(self, count: int = 1, timeout: float | None = None) -> bool:
        """获取许可"""
        async with self._lock:
            self._stats.total_requests += 1

            # 检查窗口是否需要重置
            now = time.time()
            if now - self._window_start >= 1.0:
                self._window_start = now
                self._requests_in_window = 0

            # 检查是否超过当前速率限制
            if self._requests_in_window >= self._current_rate and (timeout is None or timeout <= 0):
                self._stats.rejected_requests += 1
                return False

        # 等待下一个窗口
        if timeout is not None and timeout > 0:
            start_time = time.time()
            while True:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    async with self._lock:
                        self._stats.rejected_requests += 1
                    return False

                async with self._lock:
                    now = time.time()
                    if now - self._window_start >= 1.0:
                        self._window_start = now
                        self._requests_in_window = 0

                    if self._requests_in_window < self._current_rate:
                        self._requests_in_window += count
                        self._stats.allowed_requests += 1
                        self._update_backpressure(self._requests_in_window / self._current_rate)
                        return True

                await asyncio.sleep(0.01)

        async with self._lock:
            self._requests_in_window += count
            self._stats.allowed_requests += 1
            self._update_backpressure(self._requests_in_window / self._current_rate)
            return True

    async def release(self, count: int = 1) -> None:
        """AIMD 不需要显式释放"""
        pass

    def on_success(self) -> None:
        """成功时加性增加速率"""
        self._consecutive_successes += 1
        self._consecutive_failures = 0

        # 每 10 次连续成功增加一次速率
        if self._consecutive_successes >= 10:
            self._current_rate = min(
                self._current_rate + self._config.additive_increase,
                self._config.max_rate,
            )
            self._consecutive_successes = 0
            self._stats.current_rate = self._current_rate
            logger.debug(f"AIMD: 速率增加到 {self._current_rate}")

    def on_failure(self) -> None:
        """失败时乘性降低速率"""
        self._consecutive_failures += 1
        self._consecutive_successes = 0

        # 立即降低速率
        self._current_rate = max(
            self._current_rate * self._config.multiplicative_decrease,
            self._config.min_rate,
        )
        self._stats.current_rate = self._current_rate
        logger.debug(f"AIMD: 速率降低到 {self._current_rate}")


class SlidingWindowController(FlowController):
    """
    滑动窗口流量控制器

    特点：
    - 精确的时间窗口控制
    - 平滑的流量限制
    - 适合 API 限流
    """

    def __init__(self, config: FlowControlConfig | None = None):
        super().__init__(config)
        self._requests: list[float] = []  # 请求时间戳列表

    async def acquire(self, count: int = 1, timeout: float | None = None) -> bool:
        """获取许可"""
        async with self._lock:
            self._stats.total_requests += 1

            # 清理过期请求
            self._cleanup()

            # 检查是否超过限制
            if len(self._requests) + count <= self._config.max_requests_per_window:
                now = time.time()
                for _ in range(count):
                    self._requests.append(now)
                self._stats.allowed_requests += 1
                self._update_backpressure(
                    len(self._requests) / self._config.max_requests_per_window
                )
                self._stats.current_rate = len(self._requests) / self._config.window_size
                return True

            if timeout is None or timeout <= 0:
                self._stats.rejected_requests += 1
                return False

        # 等待窗口滑动
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                async with self._lock:
                    self._stats.rejected_requests += 1
                return False

            async with self._lock:
                self._cleanup()
                if len(self._requests) + count <= self._config.max_requests_per_window:
                    now = time.time()
                    for _ in range(count):
                        self._requests.append(now)
                    self._stats.allowed_requests += 1
                    self._update_backpressure(
                        len(self._requests) / self._config.max_requests_per_window
                    )
                    self._stats.current_rate = len(self._requests) / self._config.window_size
                    return True

            await asyncio.sleep(0.01)

    async def release(self, count: int = 1) -> None:
        """滑动窗口不需要显式释放"""
        pass

    def on_success(self) -> None:
        """滑动窗口不需要自适应调整"""
        pass

    def on_failure(self) -> None:
        """滑动窗口不需要自适应调整"""
        pass

    def _cleanup(self) -> None:
        """清理过期请求"""
        cutoff = time.time() - self._config.window_size
        self._requests = [t for t in self._requests if t > cutoff]


class BackpressureManager:
    """
    背压管理器

    协调多个流量控制器，提供统一的背压信号。
    """

    def __init__(self):
        self._controllers: dict[str, FlowController] = {}
        self._callbacks: list[Any] = []
        self._current_level = BackpressureLevel.NONE

    def register(self, name: str, controller: FlowController) -> None:
        """注册流量控制器"""
        self._controllers[name] = controller

    def unregister(self, name: str) -> None:
        """注销流量控制器"""
        self._controllers.pop(name, None)

    def on_backpressure_change(self, callback: Any) -> None:
        """注册背压变化回调"""
        self._callbacks.append(callback)

    def get_level(self) -> BackpressureLevel:
        """获取当前背压级别（取所有控制器的最高级别）"""
        if not self._controllers:
            return BackpressureLevel.NONE

        levels = [c.backpressure_level for c in self._controllers.values()]
        level_order = [
            BackpressureLevel.NONE,
            BackpressureLevel.LOW,
            BackpressureLevel.MEDIUM,
            BackpressureLevel.HIGH,
            BackpressureLevel.CRITICAL,
        ]

        max_level = BackpressureLevel.NONE
        for level in levels:
            if level_order.index(level) > level_order.index(max_level):
                max_level = level

        # 触发回调
        if max_level != self._current_level:
            old_level = self._current_level
            self._current_level = max_level
            for callback in self._callbacks:
                try:
                    callback(old_level, max_level)
                except Exception as e:
                    logger.error(f"背压回调异常: {e}")

        return max_level

    def should_pause(self) -> bool:
        """是否应该暂停处理"""
        return self.get_level() == BackpressureLevel.CRITICAL

    def get_delay_factor(self) -> float:
        """
        获取延迟因子

        Returns:
            延迟因子（1.0 表示正常，>1.0 表示需要减速）
        """
        level = self.get_level()
        factors = {
            BackpressureLevel.NONE: 1.0,
            BackpressureLevel.LOW: 1.5,
            BackpressureLevel.MEDIUM: 2.0,
            BackpressureLevel.HIGH: 4.0,
            BackpressureLevel.CRITICAL: 10.0,
        }
        return factors.get(level, 1.0)

    def get_stats(self) -> dict[str, FlowControlStats]:
        """获取所有控制器的统计信息"""
        return {name: c.stats for name, c in self._controllers.items()}


def create_flow_controller(
    strategy: FlowControlStrategy = FlowControlStrategy.TOKEN_BUCKET,
    config: FlowControlConfig | None = None,
) -> FlowController:
    """
    创建流量控制器

    Args:
        strategy: 流量控制策略
        config: 配置

    Returns:
        流量控制器实例
    """
    config = config or FlowControlConfig(strategy=strategy)

    if strategy == FlowControlStrategy.TOKEN_BUCKET:
        return TokenBucketController(config)
    elif strategy == FlowControlStrategy.AIMD:
        return AIMDController(config)
    elif strategy == FlowControlStrategy.SLIDING_WINDOW:
        return SlidingWindowController(config)
    else:
        raise ValueError(f"未知的流量控制策略: {strategy}")
