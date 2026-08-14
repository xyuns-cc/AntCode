"""认证拦截器

支持多种认证方式，按优先级尝试：
1. mTLS（如果启用）
2. API Key
3. JWT

职责拆分：凭据校验见 ``auth_credentials``，证书绑定见 ``auth_mtls``，
拒绝路径（限流 + 审计 + abort）见 ``auth_reject``，审计写入见 ``security_audit``。

**Validates: Requirements 6.2**
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import grpc
from loguru import logger

import antcode_gateway.auth_credentials as credentials
import antcode_gateway.auth_mtls as mtls
import antcode_gateway.auth_principal as auth_principal
from antcode_gateway.auth_reject import AuthRejector, UnauthenticatedRateLimiter
from antcode_gateway.security_audit import SecurityAuditor

AUTHENTICATED_WORKER_ID = auth_principal.AUTHENTICATED_WORKER_ID
AuthResult = auth_principal.AuthResult
get_authenticated_worker_id = auth_principal.get_authenticated_worker_id
require_authenticated_worker = auth_principal.require_authenticated_worker

if TYPE_CHECKING:
    from antcode_core.infrastructure.redis.stream_client import StreamClient


class AuthInterceptor(grpc.aio.ServerInterceptor):
    """认证拦截器（注册在限流拦截器**之前**，即位于调用链最外层）"""

    # 元数据键名
    API_KEY_HEADER = credentials.API_KEY_HEADER
    WORKER_ID_HEADER = credentials.WORKER_ID_HEADER
    AUTHORIZATION_HEADER = credentials.AUTHORIZATION_HEADER

    # P2-03: 严格白名单 —— 只允许下列 method 免认证,任何**新增**的 RPC 都必须
    # 明确决定是走认证还是加进这个 set,不能默认放行。intercept_service() 用
    # ``method in AUTH_EXEMPT_METHODS`` 做精确匹配,不支持前缀/正则通配,避免
    # "凡是 /grpc.health.v1.Health/* 都放行" 这种模糊边界被 accidental
    # /grpc.health.v1.Health/Drain(假想)拿去绕过鉴权。
    #
    # 当前豁免:
    # - /antcode.v1.ControlService/Register: 首次注册时 worker 还没有可放进
    #   metadata 的 api_key,拦截器层无从鉴权。**它在拦截器视角下是完全匿名的
    #   入口**:唯一防线在 handler 内部 —— ControlService.Register 用请求体里的
    #   ``request.api_key`` 调 registration_gate.registration_validation_error()
    #   (verify_api_key + Worker 状态 + V2 注册 ACK)。因此 Register 的限流与
    #   安全审计必须由它自己完成,本拦截器一概不介入。
    # - /grpc.health.v1.Health/Check|Watch: 标准 gRPC 健康检查,给
    #   grpc_health_probe / K8s L7 探针用,不携带业务凭证。
    #
    # Deregister **不**在豁免列表:必须鉴权,否则任何人都能发
    # Deregister{任意 worker_id} 撤销他人 lease 造成 DoS。
    #
    # 想新增 method?先回答: 这个 RPC 自己有没有凭证校验 + 限流 + 审计?
    # 有 → 可以加;没有 → 不要加,让它走认证。
    AUTH_EXEMPT_METHODS = frozenset(
        [
            "/antcode.v1.ControlService/Register",  # 首次注册（此时无 api_key）
            "/grpc.health.v1.Health/Check",  # gRPC 健康检查
            "/grpc.health.v1.Health/Watch",
        ]
    )

    def __init__(
        self,
        enabled: bool = True,
        *,
        api_key_validator: Callable[..., Any] | None = None,
        jwt_validator: Callable[[str], dict | None] | None = None,
        audit_stream: "StreamClient | None" = None,
        rate_limiter: UnauthenticatedRateLimiter | None = None,
    ):
        """初始化认证拦截器

        Args:
            enabled: 是否启用认证
            api_key_validator: API Key 验证函数，返回是否有效
            jwt_validator: JWT 验证函数，返回解码后的 payload 或 None
            audit_stream: 安全审计 Stream 客户端；为 None 时跳过审计写入。
            rate_limiter: 限流拦截器；为 None（``RATE_LIMIT_ENABLED=false`` 或
                单测）时认证拒绝路径不限流。
        """
        self.enabled = enabled
        self._api_key_validator = api_key_validator
        self._jwt_validator = jwt_validator
        self._rejector = AuthRejector(SecurityAuditor(audit_stream), rate_limiter)

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        """拦截服务调用进行认证"""
        if not self.enabled:
            return await self._intercept_disabled(continuation, handler_call_details)

        # P2-03: 严格白名单精确匹配 —— 未在 AUTH_EXEMPT_METHODS 里的 method
        # 一律走完整认证,不做前缀/正则通配,新增 method 默认拒绝。
        if handler_call_details.method in self.AUTH_EXEMPT_METHODS:
            return await continuation(handler_call_details)

        metadata = dict(handler_call_details.invocation_metadata)
        auth_result = await self._authenticate(metadata)
        if error := self._reject_reason(auth_result, metadata):
            return await self._build_reject_handler(
                continuation,
                handler_call_details,
                error,
                audit_metadata=metadata,
            )

        logger.debug(f"认证成功: worker_id={auth_result.worker_id}, method={auth_result.auth_method}")
        # 认证成功:对开启 mTLS 的链路再做 CN/SAN <-> authenticated worker 绑定校验。
        return await self._wrap_with_mtls_check(
            continuation,
            handler_call_details,
            auth_result.worker_id or "",
            require_certificate=auth_result.auth_method == "mtls",
        )

    def _reject_reason(self, auth_result: AuthResult, metadata: dict) -> str:
        """返回拒绝原因；空串表示放行。"""
        if not auth_result.success:
            return auth_result.error or "认证失败"
        if not auth_result.worker_id:
            return "认证凭据缺少 Worker 身份"
        # Header 只是客户端声明，必须与凭据解析出的主体完全一致。
        declared_worker_id = metadata.get(self.WORKER_ID_HEADER) or ""
        if declared_worker_id and declared_worker_id != auth_result.worker_id:
            return "X-Worker-ID 与认证主体不匹配"
        return ""

    async def _intercept_disabled(self, continuation: Callable, details: grpc.HandlerCallDetails) -> Any:
        if details.method in self.AUTH_EXEMPT_METHODS:
            return await continuation(details)
        worker_id = dict(details.invocation_metadata).get(self.WORKER_ID_HEADER) or ""
        if not worker_id:
            return await self._build_reject_handler(
                continuation,
                details,
                "AUTH_ENABLED=false 时仍必须提供 X-Worker-ID",
            )
        return await self._wrap_with_mtls_check(continuation, details, worker_id)

    # ------------------------------------------------------------------
    # 认证（实现见 auth_credentials）
    # ------------------------------------------------------------------

    async def _authenticate(self, metadata: dict) -> AuthResult:
        """按优先级尝试不同的认证方式。"""
        api_key = metadata.get(self.API_KEY_HEADER)
        worker_id = metadata.get(self.WORKER_ID_HEADER)
        explicit_auth_failure: AuthResult | None = None

        # P0-a1: API Key 认证必须显式指定 X-Worker-ID,不再从 key 前缀构造
        # fake worker_id;这样任何"只给 key 不给 worker_id"的调用都会被拒。
        if api_key:
            if not worker_id:
                return AuthResult(success=False, error="API Key 认证必须提供 X-Worker-ID header")
            result = await self._authenticate_api_key(api_key, worker_id)
            if result.success:
                return result
            explicit_auth_failure = result

        auth_header = metadata.get(self.AUTHORIZATION_HEADER)
        if auth_header and auth_header.lower().startswith(credentials.BEARER_PREFIX):
            result = await self._authenticate_jwt(auth_header[len(credentials.BEARER_PREFIX) :])
            if result.success:
                return result
            explicit_auth_failure = result

        # 显式提供的凭据必须决定认证结果，不能在校验失败后再将
        # X-Worker-ID 解释为 mTLS 主体声明。同时提供多种凭据时，返回最后
        # 实际尝试的失败（JWT 比已失败的 API Key 更接近最终结果）。
        if explicit_auth_failure is not None:
            return explicit_auth_failure

        # mTLS: intercept 阶段拿不到 ServicerContext，先接受声明的 worker_id
        # 作为待验证主体；实际 handler 必须从 auth_context 取得客户端证书，
        # 并把 CN/SAN 与该主体强绑定。没有证书不会被放行。
        if worker_id:
            return AuthResult(success=True, worker_id=worker_id, auth_method="mtls")

        return AuthResult(success=False, error="未提供有效的认证信息")

    async def _authenticate_api_key(self, api_key: str, worker_id: str | None = None) -> AuthResult:
        return await credentials.authenticate_api_key(api_key, worker_id, self._api_key_validator)

    async def _authenticate_jwt(self, token: str) -> AuthResult:
        return await credentials.authenticate_jwt(token, self._jwt_validator)

    # ------------------------------------------------------------------
    # 拒绝路径（实现见 auth_reject）
    # ------------------------------------------------------------------

    async def _build_reject_handler(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
        error: str,
        *,
        audit_metadata: dict | None = None,
    ) -> grpc.RpcMethodHandler:
        """构造认证拒绝 handler（P0-B10：限流 → 审计 → UNAUTHENTICATED）。

        ``continuation`` 仍必须调用：拒绝 handler 要与原 RPC 的 cardinality 和
        序列化器一致，否则 stream 类调用方会在 codec 层报错。
        """
        original = await continuation(handler_call_details)
        return self._rejector.build(
            original,
            error,
            method=handler_call_details.method,
            audit_metadata=audit_metadata,
        )

    def _make_reject_method_handler(
        self,
        original: grpc.RpcMethodHandler | None,
        error: str,
    ) -> grpc.RpcMethodHandler:
        """按原 handler 的 cardinality 生成 UNAUTHENTICATED handler（不含限流）。"""
        return self._rejector.make_handler(original, error)

    # ------------------------------------------------------------------
    # mTLS 绑定（实现见 auth_mtls）
    # ------------------------------------------------------------------

    async def _wrap_with_mtls_check(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
        authenticated_worker_id: str,
        *,
        require_certificate: bool = False,
    ) -> grpc.RpcMethodHandler:
        """P1-#12: 在 mTLS 链路上确保 peer 证书 CN/SAN 与认证主体一致。"""
        original = await continuation(handler_call_details)
        return self._make_mtls_wrapped_handler(
            original,
            authenticated_worker_id,
            require_certificate=require_certificate,
        )

    def _make_mtls_wrapped_handler(
        self,
        original: grpc.RpcMethodHandler,
        authenticated_worker_id: str,
        *,
        require_certificate: bool = False,
    ) -> grpc.RpcMethodHandler:
        return mtls.make_mtls_wrapped_handler(
            original,
            authenticated_worker_id,
            require_certificate=require_certificate,
        )

    @staticmethod
    def _check_mtls_binding(
        context: grpc.aio.ServicerContext,
        authenticated_worker_id: str,
        *,
        require_certificate: bool = False,
    ) -> tuple[bool, str]:
        return mtls.check_mtls_binding(
            context,
            authenticated_worker_id,
            require_certificate=require_certificate,
        )


__all__ = [
    "AUTHENTICATED_WORKER_ID",
    "AuthInterceptor",
    "AuthResult",
    "UnauthenticatedRateLimiter",
    "get_authenticated_worker_id",
    "require_authenticated_worker",
]
