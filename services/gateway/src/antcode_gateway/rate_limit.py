"""
限流模块

实现请求限流，保护后端服务：
- Redis 令牌桶（多副本共享）
- 按认证主体 / 来源 IP 限流
- 全局限流兜底

**Validates: Requirements 6.2**
"""

import os
from collections.abc import Callable
from typing import Any, cast

import grpc
from antcode_core.common.security.network_source import extract_client_ip
from loguru import logger

from antcode_gateway.auth import get_authenticated_worker_id
from antcode_gateway.rate_limit_handlers import wrap_handler
from antcode_gateway.redis_token_bucket import RedisTokenBucketLimiter

# 全局桶相对 per-key 桶的容量/速率倍数
DEFAULT_GLOBAL_MULTIPLIER = 10
# per-key 桶默认速率 / 容量
DEFAULT_PER_KEY_RATE = 100.0
DEFAULT_PER_KEY_CAPACITY = 200


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

    P0-B10: 认证失败的请求被 AuthInterceptor 在**更外层**短路,根本不会进入本
    拦截器的 ``intercept_service``。这类请求的限流由 ``wrap_unauthenticated``
    补齐 —— 见该方法的说明。
    """

    GLOBAL_MULTIPLIER = DEFAULT_GLOBAL_MULTIPLIER

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
        rate: float = DEFAULT_PER_KEY_RATE,
        capacity: int = DEFAULT_PER_KEY_CAPACITY,
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

    def wrap_unauthenticated(self, handler: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        """给认证失败的拒绝 handler 补上限流（P0-B10）。

        AuthInterceptor 位于本拦截器**外层**：认证失败时它直接返回自己构造的
        abort handler，请求根本不会流到 ``intercept_service``，因此那条路径上的
        令牌桶从来没被扣过。攻击者只要能连上网关端口，就能用伪造的 ``x-api-key``
        无限循环调用，每次都触发一轮 API Key 查库 + ``audit:security`` 写入。

        这里把拒绝 handler 交回同一套桶包一层，让 ``_check`` 在 abort 之前执行。
        此时 ``AUTHENTICATED_WORKER_ID`` 尚未写入，``_resolve_key`` 自然退回真实
        对端 IP —— 与匿名请求同桶，不会污染已认证 worker 的配额。
        """
        if not self.enabled or method in self.SKIP_RATE_LIMIT_METHODS:
            return handler
        return self._wrap_handler(handler, method)

    def _wrap_handler(self, original: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        """按 RPC 类型包上限流检查（各 cardinality 的装配见 rate_limit_handlers）。"""
        return wrap_handler(original, method, self._check)

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


__all__ = ["RateLimitInterceptor"]
