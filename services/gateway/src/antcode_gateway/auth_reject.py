"""认证拒绝路径：限流 → 安全审计 → abort(UNAUTHENTICATED)。

P0-B10：拒绝路径此前只借下游 handler 的 serializer 另造一个纯 abort handler，
限流拦截器包出来的检查永远不会执行。结果是任何人伪造 ``x-api-key`` 循环调用
``Lease``/``AckTask``，每个请求都能触发两次 PostgreSQL 查询 + 一次
``audit:security`` XADD，全程零限流 —— 这是网关上唯一无需凭据即可触发的放大点。

修复方式是把顺序倒过来：先把 abort handler 交回限流拦截器包一层，再让审计写入
发生在 abort 之前。于是超限的请求直接 RESOURCE_EXHAUSTED，既不落审计事件，也
不会继续放大后端负载。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import grpc
from loguru import logger

from antcode_gateway.auth_credentials import API_KEY_HEADER, WORKER_ID_HEADER, presented_credential_event
from antcode_gateway.grpc_rejection import make_rejection_handler
from antcode_gateway.security_audit import (
    SecurityAuditEvent,
    SecurityAuditor,
    extract_trace_id,
    sanitize_worker_id,
)

# 代理链上报的对端地址头（仅用于审计标注，不参与任何鉴权/限流决策）
FORWARDED_FOR_HEADER = "x-forwarded-for"
REAL_IP_HEADER = "x-real-ip"


class UnauthenticatedRateLimiter(Protocol):
    """认证拒绝路径的限流协作接口（由 ``RateLimitInterceptor`` 实现）。

    以 Protocol 声明而不是直接 import ``RateLimitInterceptor``：后者依赖
    ``auth.get_authenticated_worker_id``，反向 import 会形成循环。实例由
    ``main._configure_interceptors`` 这个组装根注入。
    """

    def wrap_unauthenticated(self, handler: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler: ...


class AuthRejector:
    """构造认证失败时返回的 gRPC handler。"""

    def __init__(
        self,
        auditor: SecurityAuditor,
        rate_limiter: UnauthenticatedRateLimiter | None = None,
    ) -> None:
        self._auditor = auditor
        self._rate_limiter = rate_limiter

    def build(
        self,
        original: grpc.RpcMethodHandler | None,
        error: str,
        *,
        method: str,
        audit_metadata: dict | None = None,
    ) -> grpc.RpcMethodHandler:
        """生成带限流的拒绝 handler。

        ``audit_metadata`` 为 None 时不写安全审计（例如 ``AUTH_ENABLED=false``
        下缺少 X-Worker-ID，此时根本没有凭据可审计）。
        ``rate_limiter`` 为 None 时（``RATE_LIMIT_ENABLED=false`` 或单测）不限流。
        """
        audit = None if audit_metadata is None else self._audit_callback(audit_metadata, method, error)
        handler = self.make_handler(original, error, audit=audit)
        if self._rate_limiter is None:
            return handler
        return self._rate_limiter.wrap_unauthenticated(handler, method)

    @staticmethod
    def make_handler(
        original: grpc.RpcMethodHandler | None,
        error: str,
        *,
        audit: Callable[[], Awaitable[None]] | None = None,
    ) -> grpc.RpcMethodHandler:
        """P2-#23: 按原 handler 的实际 cardinality 生成 UNAUTHENTICATED handler，
        避免对 stream 类 RPC 一刀切回 unary 导致客户端 codec 报错。

        ``audit`` 在 abort 之前执行；它被放在 handler 内部（而不是
        ``intercept_service``）正是为了让审计写入排在限流之后。
        """

        async def reject(_request_or_iterator: Any, context: grpc.aio.ServicerContext) -> None:
            if audit is not None:
                await audit()
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"认证失败: {error}")

        return make_rejection_handler(original, reject)

    def _audit_callback(self, metadata: dict, method: str, error: str) -> Callable[[], Awaitable[None]]:
        async def audit() -> None:
            await self.audit_failure(metadata, method, error)

        return audit

    async def audit_failure(self, metadata: dict, method: str, error: str) -> None:
        """记录一次认证失败（结构化日志 + ``audit:security`` 事件）。"""
        worker_id = sanitize_worker_id(metadata.get(WORKER_ID_HEADER))
        has_api_key = bool(metadata.get(API_KEY_HEADER))
        logger.warning(f"认证失败: {error}, method={method}, has_api_key={has_api_key}, worker_id={worker_id}")
        await self._auditor.emit(
            SecurityAuditEvent(
                event_type=presented_credential_event(metadata),
                worker_id=worker_id,
                peer=metadata.get(FORWARDED_FOR_HEADER) or metadata.get(REAL_IP_HEADER) or "",
                reason=error,
                trace_id=extract_trace_id(metadata),
            )
        )


__all__ = ["AuthRejector", "UnauthenticatedRateLimiter"]
