"""
限流模块

实现请求限流，保护后端服务。
支持：
- 令牌桶算法
- 按 Worker ID 限流
- 全局限流

**Validates: Requirements 6.2**
"""

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any

import grpc
from loguru import logger


@dataclass
class RateLimitResult:
    """限流结果"""

    allowed: bool
    remaining: int = 0
    reset_at: float = 0.0
    retry_after: float = 0.0


class TokenBucketLimiter:
    """令牌桶限流器

    实现令牌桶算法，支持：
    - 按键限流（如 worker_id）
    - 突发流量处理
    - 线程安全
    """

    def __init__(
        self,
        rate: float = 100.0,
        capacity: int = 200,
        key_ttl: float = 3600.0,
    ):
        """初始化限流器

        Args:
            rate: 每秒生成的令牌数
            capacity: 令牌桶容量（最大突发量）
            key_ttl: 键的过期时间（秒），用于清理不活跃的键
        """
        self.rate = rate
        self.capacity = capacity
        self.key_ttl = key_ttl

        self._tokens: dict[str, float] = defaultdict(lambda: float(capacity))
        self._last_update: dict[str, float] = defaultdict(time.time)
        self._lock = Lock()

    def allow(self, key: str = "default") -> RateLimitResult:
        """检查是否允许请求

        Args:
            key: 限流键（如 worker_id）

        Returns:
            限流结果
        """
        with self._lock:
            now = time.time()
            last = self._last_update[key]
            elapsed = now - last

            # 补充令牌
            current_tokens = min(
                self.capacity,
                self._tokens[key] + elapsed * self.rate
            )
            self._last_update[key] = now

            if self.rate <= 0:
                if current_tokens >= 1:
                    self._tokens[key] = current_tokens - 1
                    return RateLimitResult(
                        allowed=True,
                        remaining=int(self._tokens[key]),
                        reset_at=0.0,
                    )
                self._tokens[key] = current_tokens
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_at=0.0,
                    retry_after=-1.0,
                )

            # 消耗令牌
            if current_tokens >= 1:
                self._tokens[key] = current_tokens - 1
                return RateLimitResult(
                    allowed=True,
                    remaining=int(self._tokens[key]),
                    reset_at=now + (self.capacity - self._tokens[key]) / self.rate,
                )

            # 计算需要等待的时间
            self._tokens[key] = current_tokens
            retry_after = (1 - current_tokens) / self.rate

            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_at=now + retry_after,
                retry_after=retry_after,
            )

    def cleanup(self, max_age: float | None = None) -> int:
        """清理过期的键

        Args:
            max_age: 最大年龄（秒），默认使用 key_ttl

        Returns:
            清理的键数量
        """
        max_age = max_age or self.key_ttl
        now = time.time()
        cleaned = 0

        with self._lock:
            keys_to_remove = [
                key for key, last_update in self._last_update.items()
                if now - last_update > max_age
            ]

            for key in keys_to_remove:
                del self._tokens[key]
                del self._last_update[key]
                cleaned += 1

        if cleaned > 0:
            logger.debug(f"清理了 {cleaned} 个过期的限流键")

        return cleaned


class RateLimiter:
    """组合限流器

    支持多级限流：
    - 全局限流
    - 按 Worker 限流
    """

    def __init__(
        self,
        global_rate: float = 1000.0,
        global_capacity: int = 2000,
        per_worker_rate: float = 100.0,
        per_worker_capacity: int = 200,
    ):
        """初始化限流器

        Args:
            global_rate: 全局每秒请求数
            global_capacity: 全局令牌桶容量
            per_worker_rate: 每个 Worker 每秒请求数
            per_worker_capacity: 每个 Worker 令牌桶容量
        """
        self.global_limiter = TokenBucketLimiter(
            rate=global_rate,
            capacity=global_capacity,
        )
        self.worker_limiter = TokenBucketLimiter(
            rate=per_worker_rate,
            capacity=per_worker_capacity,
        )

    def allow(self, worker_id: str = "default") -> RateLimitResult:
        """检查是否允许请求

        Args:
            worker_id: Worker ID

        Returns:
            限流结果
        """
        # 先检查全局限流
        global_result = self.global_limiter.allow("global")
        if not global_result.allowed:
            return global_result

        # 再检查 Worker 限流
        return self.worker_limiter.allow(worker_id)


class RateLimitInterceptor(grpc.aio.ServerInterceptor):
    """限流拦截器

    在 gRPC 层实现请求限流。
    """

    # 元数据键名
    WORKER_ID_HEADER = "x-worker-id"

    # 不需要限流的方法
    SKIP_RATE_LIMIT_METHODS = frozenset([
        "/grpc.health.v1.Health/Check",
        "/grpc.health.v1.Health/Watch",
    ])

    def __init__(
        self,
        enabled: bool = True,
        rate: float = 100.0,
        capacity: int = 200,
    ):
        """初始化限流拦截器

        Args:
            enabled: 是否启用限流
            rate: 每秒请求数
            capacity: 令牌桶容量
        """
        self.enabled = enabled
        self.limiter = TokenBucketLimiter(rate=rate, capacity=capacity)

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        """拦截服务调用进行限流"""
        if not self.enabled:
            return await continuation(handler_call_details)

        # 检查是否跳过限流
        method = handler_call_details.method
        if method in self.SKIP_RATE_LIMIT_METHODS:
            return await continuation(handler_call_details)

        # 获取限流键
        metadata = dict(handler_call_details.invocation_metadata)
        key = metadata.get(self.WORKER_ID_HEADER, "default")

        # 检查限流
        result = self.limiter.allow(key)

        if not result.allowed:
            logger.warning(
                f"请求被限流: key={key}, method={method}, "
                f"retry_after={result.retry_after:.2f}s"
            )
            return self._create_rate_limited_handler(result)

        # 继续处理
        return await continuation(handler_call_details)

    def _create_rate_limited_handler(
        self,
        result: RateLimitResult,
    ) -> grpc.RpcMethodHandler:
        """创建限流响应处理器"""

        async def rate_limited_handler(request, context):
            # 设置重试时间
            context.set_trailing_metadata([
                ("retry-after", str(int(result.retry_after) + 1)),
                ("x-ratelimit-remaining", "0"),
                ("x-ratelimit-reset", str(int(result.reset_at))),
            ])
            await context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                f"请求过于频繁，请在 {result.retry_after:.1f} 秒后重试",
            )

        return grpc.unary_unary_rpc_method_handler(rate_limited_handler)
