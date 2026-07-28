"""
限流模块

实现请求限流，保护后端服务。
支持：
- 令牌桶算法
- 按 Worker ID 限流
- 全局限流

**Validates: Requirements 6.2**
"""

import os
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any, cast

import grpc
from antcode_core.common.security.network_source import extract_client_ip
from loguru import logger

from antcode_gateway.auth import get_authenticated_worker_id
from antcode_gateway.redis_token_bucket import RedisTokenBucketLimiter


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
            current_tokens = min(self.capacity, self._tokens[key] + elapsed * self.rate)
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
            keys_to_remove = [key for key, last_update in self._last_update.items() if now - last_update > max_age]

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

    P0(DoS): 限流键必须从**不可伪造**的来源派生,否则匿名调用者(如 auth-exempt
    的 Register)可以每次请求换一个 ``x-worker-id`` / ``x-forwarded-for`` 头,每次
    都拿到一个全新的令牌桶,从而彻底绕过 per-key 限流打爆 DB/CPU。因此:

    - 已认证方法: 用 AuthInterceptor 校验并写入 contextvar 的**服务端认证主体**
      (``get_authenticated_worker_id``),而非客户端声明的 header。
    - 未认证/匿名方法: 退回到真实传输对端 IP (``context.peer()``);仅当直连对端
      命中 ``ANTCODE_TRUSTED_PROXIES`` 白名单时才采信 XFF/X-Real-IP(复用 web_api
      侧同一个 ``extract_client_ip``,取代理链里最右侧的非受信跳)。
    - 额外叠加一个全局令牌桶上限: 即使攻击者不停变换 key,单一网关的总吞吐仍被封顶。

    限流检查下沉到实际 RPC 调用阶段(而非 intercept_service),因为 ``ServicerContext``
    只有在 handler 执行时才可用——此时才拿得到 ``context.peer()`` 与 AuthInterceptor
    写入的认证主体(AuthInterceptor 在外层,其 wrap 的 handler 先于本拦截器 handler
    运行,contextvar 已就绪)。
    """

    # 全局桶相对 per-key 桶的容量/速率倍数(与内存版 RateLimiter 的 10x 默认一致)。
    GLOBAL_MULTIPLIER = 10

    # 不需要限流的方法
    SKIP_RATE_LIMIT_METHODS = frozenset(
        [
            "/grpc.health.v1.Health/Check",
            "/grpc.health.v1.Health/Watch",
        ]
    )

    def __init__(
        self,
        enabled: bool = True,
        rate: float = 100.0,
        capacity: int = 200,
        redis_client: Any | None = None,
        *,
        global_rate: float | None = None,
        global_capacity: int | None = None,
    ):
        """初始化限流拦截器

        Args:
            enabled: 是否启用限流
            rate: 每 key 每秒请求数
            capacity: 每 key 令牌桶容量
            redis_client: 复用的 Redis 客户端(不传则惰性获取)
            global_rate: 全局每秒请求数(不传默认 rate * GLOBAL_MULTIPLIER)
            global_capacity: 全局令牌桶容量(不传默认 capacity * GLOBAL_MULTIPLIER)
        """
        self.enabled = enabled
        self._rate = rate
        self._capacity = capacity
        self._redis_client = redis_client
        self._global_rate = global_rate if global_rate is not None else rate * self.GLOBAL_MULTIPLIER
        self._global_capacity = (
            global_capacity if global_capacity is not None else max(1, capacity * self.GLOBAL_MULTIPLIER)
        )
        self._limiter: RedisTokenBucketLimiter | None = None
        self._global_limiter: RedisTokenBucketLimiter | None = None

    async def _ensure_redis(self) -> Any:
        if self._redis_client is None:
            from antcode_core.infrastructure.redis.client import get_redis_client

            self._redis_client = await get_redis_client()
        return self._redis_client

    async def _build_limiter(self, rate: float, capacity: int, suffix: str) -> RedisTokenBucketLimiter:
        redis_client = await self._ensure_redis()
        from antcode_core.infrastructure.redis.control_plane import redis_namespace

        return RedisTokenBucketLimiter(
            cast(Any, redis_client),
            rate=rate,
            capacity=capacity,
            key_prefix=f"{redis_namespace()}:gateway:rate-limit{suffix}",
        )

    async def _get_limiter(self) -> RedisTokenBucketLimiter:
        if self._limiter is None:
            self._limiter = await self._build_limiter(self._rate, self._capacity, "")
        return self._limiter

    async def _get_global_limiter(self) -> RedisTokenBucketLimiter:
        if self._global_limiter is None:
            self._global_limiter = await self._build_limiter(self._global_rate, self._global_capacity, ":global")
        return self._global_limiter

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        """拦截服务调用进行限流"""
        if not self.enabled:
            return await continuation(handler_call_details)

        method = handler_call_details.method
        if method in self.SKIP_RATE_LIMIT_METHODS:
            return await continuation(handler_call_details)

        original = await continuation(handler_call_details)
        return self._wrap_handler(original, method)

    def _wrap_handler(self, original: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        """按 RPC 类型分发到对应的限流包装器（各类型逻辑见 _wrap_* 方法）。"""
        if original.unary_unary is not None:
            return self._wrap_unary_unary(original, method)
        if original.unary_stream is not None:
            return self._wrap_unary_stream(original, method)
        if original.stream_unary is not None:
            return self._wrap_stream_unary(original, method)
        if original.stream_stream is not None:
            return self._wrap_stream_stream(original, method)
        return original

    def _wrap_unary_unary(self, original: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        async def handler(request, context):
            await self._check(context, method)
            return await original.unary_unary(request, context)

        return grpc.unary_unary_rpc_method_handler(
            handler,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    def _wrap_unary_stream(self, original: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        async def handler(request, context):
            await self._check(context, method)
            async for response in original.unary_stream(request, context):
                yield response

        return grpc.unary_stream_rpc_method_handler(
            handler,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    def _wrap_stream_unary(self, original: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        async def handler(request_iterator, context):
            await self._check(context, method)
            metered = self._metered_requests(request_iterator, context, method)
            return await original.stream_unary(metered, context)

        return grpc.stream_unary_rpc_method_handler(
            handler,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    def _wrap_stream_stream(self, original: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        async def handler(request_iterator, context):
            await self._check(context, method)
            metered = self._metered_requests(request_iterator, context, method)
            async for response in original.stream_stream(metered, context):
                yield response

        return grpc.stream_stream_rpc_method_handler(
            handler,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    async def _metered_requests(self, request_iterator, context, method: str):
        """P1-GW-05: client-streaming 逐帧计费。

        此前只在建流时收费一次，持有有效凭据的客户端可用单条
        StreamStatus/StreamLogs/StreamSpiderData/Artifact upload 流持续写
        Redis/数据库绕过请求桶。现在每帧过一次全局+per-key 桶，超限
        abort(RESOURCE_EXHAUSTED)，worker 按普通流错误退避重试。
        """
        async for request in request_iterator:
            await self._check(context, method)
            yield request

    async def _check(self, context: grpc.aio.ServicerContext, method: str) -> None:
        """全局 + per-key 两级限流,超限则 abort(RESOURCE_EXHAUSTED)。"""
        metadata = dict(context.invocation_metadata() or ())
        key = self._resolve_key(metadata, context)

        # 全局上限先行: 即使 key 被不断变换,网关总吞吐仍被封顶。
        global_result = await (await self._get_global_limiter()).allow("global")
        if not global_result.allowed:
            await self._abort(context, global_result, key="global", method=method)
            return

        result = await (await self._get_limiter()).allow(key)
        if not result.allowed:
            await self._abort(context, result, key=key, method=method)

    def _resolve_key(self, metadata: dict, context: grpc.aio.ServicerContext) -> str:
        """派生限流键。

        优先用服务端认证主体(不可伪造);匿名/未认证调用退回真实对端 IP。
        绝不使用客户端声明的 ``x-worker-id`` header 作为键。
        """
        worker_id = get_authenticated_worker_id()
        if worker_id:
            return f"worker:{worker_id}"
        return self._peer_key(metadata, context)

    def _peer_key(self, metadata: dict, context: grpc.aio.ServicerContext) -> str:
        try:
            peer = context.peer() or ""
        except Exception:  # noqa: BLE001 - 拿不到 peer 不应阻断请求,退回共享匿名桶
            peer = ""
        direct_ip = self._peer_ip(peer)
        if not direct_ip:
            # 非 IP 传输(unix socket 等): 用原始 peer 串,仍不可伪造。
            return f"peer:{peer}" if peer else "anonymous"
        try:
            client_ip = extract_client_ip(
                direct_ip,
                str(metadata.get("x-forwarded-for", "") or ""),
                str(metadata.get("x-real-ip", "") or ""),
                trusted_proxies=os.getenv("ANTCODE_TRUSTED_PROXIES", ""),
            )
        except ValueError:
            # XFF/X-Real-IP 非法时退回真实对端 IP,绝不因伪造头放行。
            client_ip = direct_ip
        return f"ip:{client_ip}"

    @staticmethod
    def _peer_ip(peer: str) -> str:
        """从 grpc ``context.peer()`` 串解析出对端 IP。

        形如 ``ipv4:1.2.3.4:5678`` / ``ipv6:[::1]:5678``;其它(unix 等)返回 ""。
        """
        if peer.startswith("ipv4:"):
            return peer[5:].rsplit(":", 1)[0]
        if peer.startswith("ipv6:"):
            rest = peer[5:]
            if rest.startswith("["):
                return rest[1:].split("]", 1)[0]
            return rest.rsplit(":", 1)[0]
        return ""

    async def _abort(
        self,
        context: grpc.aio.ServicerContext,
        result: Any,
        *,
        key: str,
        method: str,
    ) -> None:
        logger.warning(f"请求被限流: key={key}, method={method}, retry_after={result.retry_after:.2f}s")
        context.set_trailing_metadata(
            [
                ("retry-after", str(int(result.retry_after) + 1)),
                ("x-ratelimit-remaining", "0"),
                ("x-ratelimit-reset", str(int(result.reset_at))),
            ]
        )
        await context.abort(
            grpc.StatusCode.RESOURCE_EXHAUSTED,
            f"请求过于频繁，请在 {result.retry_after:.1f} 秒后重试",
        )
