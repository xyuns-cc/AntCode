"""
认证模块

支持多种认证方式：
- mTLS: 双向 TLS 认证（通过客户端证书）
- API Key: API 密钥认证
- JWT: JSON Web Token 认证

**Validates: Requirements 6.2**
"""

import inspect
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import grpc
from antcode_core.observability.tracing import parse_traceparent
from loguru import logger

if TYPE_CHECKING:
    from antcode_core.infrastructure.redis.stream_client import StreamClient


# 安全审计 Stream 键名
AUDIT_SECURITY_STREAM = "audit:security"
# 审计 Stream 最大长度（近似裁剪）
AUDIT_SECURITY_MAXLEN = 100_000

# W3C TraceContext metadata header(grpc 元数据按 HTTP/2 习惯全小写)
TRACEPARENT_HEADER = "traceparent"

_AUTHENTICATED_WORKER_ID: ContextVar[str | None] = ContextVar(
    "antcode_authenticated_worker_id",
    default=None,
)


def _extract_trace_id_from_metadata(metadata: dict[str, Any]) -> str | None:
    """从 gRPC invocation_metadata 提取 W3C trace_id。

    metadata 头大小写在不同 grpc 客户端实现里不一致,这里两种都试。
    格式不合规直接返回 None,不抛——审计路径绝不能因可观测性失败而中断。
    """
    raw = metadata.get(TRACEPARENT_HEADER) or metadata.get(TRACEPARENT_HEADER.upper())
    if not raw:
        return None
    ids = parse_traceparent(str(raw))
    return ids.trace_id if ids else None


@dataclass
class AuthResult:
    """认证结果"""

    success: bool
    worker_id: str | None = None
    error: str | None = None
    auth_method: str | None = None  # "api_key", "jwt", "mtls"


def get_authenticated_worker_id() -> str | None:
    """Return the server-verified Worker identity for the active RPC."""
    return _AUTHENTICATED_WORKER_ID.get()


async def require_authenticated_worker(
    context: grpc.aio.ServicerContext,
    declared_worker_id: str | None = None,
) -> str:
    """Require the active RPC principal and optionally bind a request field to it."""
    worker_id = get_authenticated_worker_id()
    if not worker_id:
        await context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "authenticated worker identity is missing",
        )
        return ""
    if declared_worker_id and declared_worker_id != worker_id:
        await context.abort(
            grpc.StatusCode.PERMISSION_DENIED,
            "worker_id does not match authenticated principal",
        )
        return ""
    return worker_id


class AuthInterceptor(grpc.aio.ServerInterceptor):
    """认证拦截器

    支持多种认证方式，按优先级尝试：
    1. mTLS（如果启用）
    2. API Key
    3. JWT
    """

    # 元数据键名
    API_KEY_HEADER = "x-api-key"
    WORKER_ID_HEADER = "x-worker-id"
    AUTHORIZATION_HEADER = "authorization"

    # P2-03: 严格白名单 —— 只允许下列 method 免认证,任何**新增**的 RPC 都必须
    # 明确决定是走认证还是加进这个 set,不能默认放行。intercept_service() 用
    # ``method in AUTH_EXEMPT_METHODS`` 做精确匹配,不支持前缀/正则通配,避免
    # "凡是 /grpc.health.v1.Health/* 都放行" 这种模糊边界被 accidental
    # /grpc.health.v1.Health/Drain(假想)拿去绕过鉴权。
    #
    # 当前豁免:
    # - /antcode.v1.ControlService/Register: 首次注册,此时 worker 尚未拿到
    #   api_key,只能允许一次匿名调用。Register 内部有 install_key 一次性
    #   token 校验,不构成完全匿名入口。
    # - /grpc.health.v1.Health/Check|Watch: 标准 gRPC 健康检查,给
    #   grpc_health_probe / K8s L7 探针用,不携带业务凭证。
    #
    # Deregister **不**在豁免列表:必须鉴权,否则任何人都能发
    # Deregister{任意 worker_id} 撤销他人 lease 造成 DoS。
    #
    # 想新增 method?先回答: 这个 RPC 有没有独立的凭证/一次性 token 校验?
    # 有 → 可以加;没有 → 不要加,让它走认证。
    AUTH_EXEMPT_METHODS = frozenset(
        [
            "/antcode.v1.ControlService/Register",  # 首次注册（此时无 api_key）
            "/grpc.health.v1.Health/Check",  # gRPC 健康检查
            "/grpc.health.v1.Health/Watch",
        ]
    )

    # 兼容旧命名（仅本模块内部曾用；保留避免误伤外部引用）
    SKIP_AUTH_METHODS = AUTH_EXEMPT_METHODS

    def __init__(
        self,
        enabled: bool = True,
        api_key_validator: Callable[..., Any] | None = None,
        jwt_validator: Callable[[str], dict | None] | None = None,
        audit_stream: "StreamClient | None" = None,
    ):
        """初始化认证拦截器

        Args:
            enabled: 是否启用认证
            api_key_validator: API Key 验证函数，返回是否有效
            jwt_validator: JWT 验证函数，返回解码后的 payload 或 None
            audit_stream: 安全审计 Stream 客户端。如果为 None，审计写入将被跳过
                （zero-config 友好），但认证主流程不受影响。
        """
        self.enabled = enabled
        self._api_key_validator = api_key_validator
        self._jwt_validator = jwt_validator
        self._audit_stream = audit_stream

    async def _emit_audit(
        self,
        event_type: str,
        worker_id: str | None,
        peer: str | None,
        reason: str,
        trace_id: str | None = None,
    ) -> None:
        """写一条安全审计事件到 Redis Stream

        审计失败绝不能影响主认证流程，所有异常都被捕获并降级为 error 日志。

        Args:
            event_type: 事件类型，如 mtls_reject / api_key_invalid /
                jwt_invalid / install_key_replay
            worker_id: 关联的 worker_id，可空
            peer: 对端标识（IP 或 mTLS CN），可空
            reason: 简短失败原因
            trace_id: W3C trace_id（32 hex），从请求 metadata 中的
                ``traceparent`` 头提取。空时审计事件不带 trace_id 字段。
        """
        if self._audit_stream is None:
            return
        try:
            # P2-#27: peer/reason 截断, 防止恶意输入撑爆 audit Stream
            fields = {
                "event_type": event_type,
                "worker_id": (worker_id or "")[:128],
                "peer": (peer or "")[:200],
                "reason": (reason or "")[:500],
                "ts": str(int(time.time() * 1000)),
            }
            if trace_id:
                fields["trace_id"] = trace_id
            await self._audit_stream.xadd(
                AUDIT_SECURITY_STREAM,
                fields,
                maxlen=AUDIT_SECURITY_MAXLEN,
                approximate=True,
            )
        except Exception as e:  # noqa: BLE001 - 审计失败不影响主流程
            logger.exception(f"写入安全审计 Stream 失败: event_type={event_type}, error={e}")

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
        method = handler_call_details.method
        if method in self.AUTH_EXEMPT_METHODS:
            return await continuation(handler_call_details)

        # 获取元数据
        metadata = dict(handler_call_details.invocation_metadata)

        # 执行认证
        auth_result = await self._authenticate(metadata)

        if not auth_result.success:
            await self._audit_auth_failure(metadata, method, auth_result)
            return await self._build_reject_handler(continuation, handler_call_details, auth_result.error or "")

        logger.debug(f"认证成功: worker_id={auth_result.worker_id}, method={auth_result.auth_method}")

        authenticated_worker_id = auth_result.worker_id or ""
        if not authenticated_worker_id:
            return await self._build_reject_handler(
                continuation,
                handler_call_details,
                "认证凭据缺少 Worker 身份",
            )

        # Header 只是客户端声明，必须与凭据解析出的主体完全一致。
        declared_worker_id = metadata.get(self.WORKER_ID_HEADER) or ""
        if declared_worker_id and declared_worker_id != authenticated_worker_id:
            return await self._build_reject_handler(
                continuation,
                handler_call_details,
                "X-Worker-ID 与认证主体不匹配",
            )

        # 认证成功:对开启 mTLS 的链路再做 CN/SAN <-> authenticated worker 绑定校验。
        # 由于 intercept_service 阶段拿不到 ServicerContext,这里 wrap 一层
        # handler,在实际 RPC 调用时校验 peer 证书。
        return await self._wrap_with_mtls_check(
            continuation=continuation,
            handler_call_details=handler_call_details,
            authenticated_worker_id=authenticated_worker_id,
            require_certificate=auth_result.auth_method == "mtls",
        )

    async def _intercept_disabled(self, continuation: Callable, details: grpc.HandlerCallDetails) -> Any:
        if details.method in self.AUTH_EXEMPT_METHODS:
            return await continuation(details)
        metadata = dict(details.invocation_metadata)
        worker_id = metadata.get(self.WORKER_ID_HEADER) or ""
        if not worker_id:
            return await self._build_reject_handler(
                continuation,
                details,
                "AUTH_ENABLED=false 时仍必须提供 X-Worker-ID",
            )
        return await self._wrap_with_mtls_check(continuation, details, worker_id)

    async def _audit_auth_failure(self, metadata: dict, method: str, result: AuthResult) -> None:
        has_api_key = bool(metadata.get(self.API_KEY_HEADER))
        worker_id = self._sanitize_worker_id(metadata.get(self.WORKER_ID_HEADER))
        logger.warning(f"认证失败: {result.error}, method={method}, has_api_key={has_api_key}, worker_id={worker_id}")
        auth_header = metadata.get(self.AUTHORIZATION_HEADER) or ""
        if has_api_key:
            event_type = "api_key_invalid"
        elif auth_header.lower().startswith("bearer "):
            event_type = "jwt_invalid"
        else:
            event_type = "mtls_reject"
        await self._emit_audit(
            event_type=event_type,
            worker_id=worker_id,
            peer=metadata.get("x-forwarded-for") or metadata.get("x-real-ip") or "",
            reason=result.error or "",
            trace_id=_extract_trace_id_from_metadata(metadata),
        )

    async def _authenticate(self, metadata: dict) -> AuthResult:
        """执行认证

        按优先级尝试不同的认证方式。
        """
        # 1. 尝试 API Key 认证
        api_key = metadata.get(self.API_KEY_HEADER)
        worker_id = metadata.get(self.WORKER_ID_HEADER)

        # P0-a1: API Key 认证必须显式指定 X-Worker-ID,不再从 key 前缀构造
        # fake worker_id;这样任何"只给 key 不给 worker_id"的调用都会被拒。
        if api_key:
            if not worker_id:
                return AuthResult(
                    success=False,
                    error="API Key 认证必须提供 X-Worker-ID header",
                )
            result = await self._authenticate_api_key(api_key, worker_id)
            if result.success:
                return result

        # 2. 尝试 JWT 认证
        auth_header = metadata.get(self.AUTHORIZATION_HEADER)
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            result = await self._authenticate_jwt(token)
            if result.success:
                return result

        # 3. mTLS: intercept 阶段拿不到 ServicerContext，先接受声明的
        # worker_id 作为待验证主体；实际 handler 必须从 auth_context 取得
        # 客户端证书，并把 CN/SAN 与该主体强绑定。没有证书不会被放行。
        if worker_id:
            return AuthResult(success=True, worker_id=worker_id, auth_method="mtls")

        return AuthResult(success=False, error="未提供有效的认证信息")

    async def _authenticate_api_key(
        self,
        api_key: str,
        worker_id: str | None = None,
    ) -> AuthResult:
        """API Key 认证"""
        if not api_key:
            return AuthResult(success=False, error="API Key 为空")

        # 使用自定义验证器
        # P0-a1: 严格要求 worker_id 参与绑定,不再对无 worker_id 的调用做兜底。
        # 上层 _authenticate 已在 API Key 分支预先校验 worker_id 非空,这里
        # 二次防御(直接绕过入口调用本方法的路径也不会失守)。
        if not worker_id:
            return AuthResult(success=False, error="API Key 认证缺少 X-Worker-ID")

        if self._api_key_validator:
            return await self._authenticate_custom_api_key(api_key, worker_id)
        return await self._authenticate_default_api_key(api_key, worker_id)

    async def _authenticate_custom_api_key(self, api_key: str, worker_id: str) -> AuthResult:
        try:
            result, bound_worker = await self._call_api_key_validator(api_key, worker_id)
            return self._normalize_api_key_result(result, worker_id, bound_worker)
        except Exception as e:
            logger.exception(f"API Key 验证异常: {e}")
            return AuthResult(success=False, error="API Key 验证失败")

    async def _authenticate_default_api_key(self, api_key: str, worker_id: str) -> AuthResult:
        try:
            from antcode_core.common.security import verify_api_key

            valid = await verify_api_key(api_key, worker_id)
            return AuthResult(
                success=valid,
                worker_id=worker_id if valid else None,
                auth_method="api_key" if valid else None,
                error=None if valid else "无效的 API Key",
            )
        except ImportError as exc:
            logger.exception(f"安全模块不可用,拒绝认证: {exc}")
            return AuthResult(success=False, error="security_module_unavailable")
        except Exception as exc:
            logger.exception(f"API Key 验证异常: {exc}")
            return AuthResult(success=False, error="API Key 验证失败")

    async def _call_api_key_validator(
        self,
        api_key: str,
        worker_id: str,
    ) -> tuple[Any, bool]:
        validator = self._api_key_validator
        if validator is None:
            return None, False
        try:
            inspect.signature(validator).bind(api_key, worker_id)
        except (TypeError, ValueError):
            result = validator(api_key)
            bound_worker = False
        else:
            result = validator(api_key, worker_id)
            bound_worker = True
        if inspect.isawaitable(result):
            result = await result
        return result, bound_worker

    @staticmethod
    def _normalize_api_key_result(
        result: Any,
        declared_worker_id: str,
        validator_bound_worker: bool,
    ) -> AuthResult:
        normalized = AuthResult(success=False, error="无效的 API Key")
        if isinstance(result, AuthResult):
            normalized = result
            if not result.success or result.worker_id != declared_worker_id:
                normalized = AuthResult(success=False, error="API Key 与 Worker 身份不匹配")
        elif isinstance(result, dict):
            identity = result.get("worker_id") or result.get("sub")
            if identity == declared_worker_id:
                normalized = AuthResult(True, declared_worker_id, auth_method="api_key")
            else:
                normalized = AuthResult(success=False, error="API Key 与 Worker 身份不匹配")
        elif isinstance(result, str):
            if result == declared_worker_id:
                normalized = AuthResult(True, declared_worker_id, auth_method="api_key")
            else:
                normalized = AuthResult(success=False, error="API Key 与 Worker 身份不匹配")
        elif isinstance(result, bool) and validator_bound_worker and result:
            normalized = AuthResult(True, declared_worker_id, auth_method="api_key")
        elif isinstance(result, bool) and not validator_bound_worker and result:
            normalized = AuthResult(
                success=False,
                error="自定义 API Key validator 必须绑定 Worker 身份",
            )
        return normalized

    async def _authenticate_jwt(self, token: str) -> AuthResult:
        """JWT 认证"""
        if not token:
            return AuthResult(success=False, error="JWT token 为空")

        if self._jwt_validator:
            return self._authenticate_custom_jwt(token)
        return self._authenticate_default_jwt(token)

    def _authenticate_custom_jwt(self, token: str) -> AuthResult:
        try:
            payload = self._jwt_validator(token) if self._jwt_validator else None
            if not payload:
                return AuthResult(success=False, error="无效的 JWT token")
            if payload.get("token_class") != "worker":
                return AuthResult(success=False, error="自定义 JWT validator 必须验证 Worker 专用 token_class")
            worker_id = payload.get("worker_id") or payload.get("sub")
            if not worker_id:
                return AuthResult(success=False, error="自定义 JWT validator 未返回 Worker 身份")
            return AuthResult(success=True, worker_id=worker_id, auth_method="jwt")
        except Exception as exc:
            logger.exception(f"JWT 验证异常: {exc}")
            return AuthResult(success=False, error="JWT 验证失败")

    @staticmethod
    def _authenticate_default_jwt(token: str) -> AuthResult:
        try:
            from antcode_core.common.security.auth import jwt_auth

            try:
                token_data = jwt_auth.verify_token(
                    token,
                    expected_type="access",
                    expected_class="worker",
                )
            except Exception as verify_exc:  # noqa: BLE001 - HTTPException / AuthError 都归为无效
                logger.warning(f"Worker JWT 校验失败: {verify_exc}")
                return AuthResult(success=False, error="无效的 Worker JWT token")

            # Worker token 里 username 已由 verify_token 设为 worker_id;为防
            # 万一,严格取 username 字段
            worker_id = getattr(token_data, "username", None)
            if not worker_id:
                return AuthResult(success=False, error="Worker JWT 缺少 worker_id")
            return AuthResult(
                success=True,
                worker_id=worker_id,
                auth_method="jwt",
            )
        except ImportError as exc:
            logger.exception(f"安全模块不可用,拒绝认证: {exc}")
            return AuthResult(success=False, error="security_module_unavailable")
        except Exception as exc:
            logger.exception(f"JWT 验证异常: {exc}")
            return AuthResult(success=False, error="JWT 验证失败")

    @staticmethod
    def _sanitize_worker_id(worker_id: str | None) -> str:
        """worker_id 进入日志/审计前的安全清洗:剥换行 + 限长,防注入。"""
        return (worker_id or "").replace("\n", "\\n").replace("\r", "\\r")[:128]

    async def _build_reject_handler(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
        error: str,
    ) -> grpc.RpcMethodHandler:
        """P2-#23: 根据原 handler 的实际类型生成对应的 reject handler,
        避免对 stream 类 RPC 一刀切回 unary,导致客户端 codec 报错。
        """
        try:
            original = await continuation(handler_call_details)
        except Exception:  # noqa: BLE001 - 取不到 handler 时退回 unary,reject 还是会触发
            original = None

        return self._make_reject_method_handler(original, error)

    @staticmethod
    def _make_reject_method_handler(
        original: grpc.RpcMethodHandler | None,
        error: str,
    ) -> grpc.RpcMethodHandler:
        async def unary_reject(request, context):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"认证失败: {error}")

        async def stream_reject(request_iterator, context):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"认证失败: {error}")

        # 没拿到原 handler(罕见)时退回 unary——任意 RPC 触发都会被 abort。
        if original is None:
            return grpc.unary_unary_rpc_method_handler(unary_reject)

        if original.unary_unary is not None:
            return grpc.unary_unary_rpc_method_handler(
                unary_reject,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        if original.unary_stream is not None:
            return grpc.unary_stream_rpc_method_handler(
                unary_reject,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        if original.stream_unary is not None:
            return grpc.stream_unary_rpc_method_handler(
                stream_reject,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        if original.stream_stream is not None:
            return grpc.stream_stream_rpc_method_handler(
                stream_reject,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        # 兜底:未知类型仍然回 unary。
        return grpc.unary_unary_rpc_method_handler(unary_reject)

    async def _wrap_with_mtls_check(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
        authenticated_worker_id: str,
        *,
        require_certificate: bool = False,
    ) -> grpc.RpcMethodHandler:
        """P1-#12: 在 mTLS 链路上,确保 peer 证书 CN/SAN 与 metadata 中的
        worker_id 一致。开发环境(无客户端证书)直接放行,生产环境(挂了
        require_client_auth=True)证书必现,不一致即 PERMISSION_DENIED。
        """
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
        if original.unary_unary is not None:
            return self._wrap_unary_unary_handler(
                original,
                authenticated_worker_id,
                require_certificate=require_certificate,
            )
        if original.unary_stream is not None:
            return self._wrap_unary_stream_handler(
                original,
                authenticated_worker_id,
                require_certificate=require_certificate,
            )
        if original.stream_unary is not None:
            return self._wrap_stream_unary_handler(
                original,
                authenticated_worker_id,
                require_certificate=require_certificate,
            )
        if original.stream_stream is not None:
            return self._wrap_stream_stream_handler(
                original,
                authenticated_worker_id,
                require_certificate=require_certificate,
            )
        return original

    async def _require_mtls_binding(
        self,
        context,
        worker_id: str,
        *,
        require_certificate: bool = False,
    ) -> None:
        ok, reason = self._check_mtls_binding(
            context,
            worker_id,
            require_certificate=require_certificate,
        )
        if not ok:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, reason)

    def _wrap_unary_unary_handler(self, original, worker_id: str, *, require_certificate: bool = False):
        async def wrapped(request, context):
            await self._require_mtls_binding(context, worker_id, require_certificate=require_certificate)
            token = _AUTHENTICATED_WORKER_ID.set(worker_id)
            try:
                return await original.unary_unary(request, context)
            finally:
                _AUTHENTICATED_WORKER_ID.reset(token)

        return grpc.unary_unary_rpc_method_handler(
            wrapped,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    def _wrap_unary_stream_handler(self, original, worker_id: str, *, require_certificate: bool = False):
        async def wrapped(request, context):
            await self._require_mtls_binding(context, worker_id, require_certificate=require_certificate)
            token = _AUTHENTICATED_WORKER_ID.set(worker_id)
            try:
                async for response in original.unary_stream(request, context):
                    yield response
            finally:
                _AUTHENTICATED_WORKER_ID.reset(token)

        return grpc.unary_stream_rpc_method_handler(
            wrapped,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    def _wrap_stream_unary_handler(self, original, worker_id: str, *, require_certificate: bool = False):
        async def wrapped(request_iterator, context):
            await self._require_mtls_binding(context, worker_id, require_certificate=require_certificate)
            token = _AUTHENTICATED_WORKER_ID.set(worker_id)
            try:
                return await original.stream_unary(request_iterator, context)
            finally:
                _AUTHENTICATED_WORKER_ID.reset(token)

        return grpc.stream_unary_rpc_method_handler(
            wrapped,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    def _wrap_stream_stream_handler(self, original, worker_id: str, *, require_certificate: bool = False):
        async def wrapped(request_iterator, context):
            await self._require_mtls_binding(context, worker_id, require_certificate=require_certificate)
            token = _AUTHENTICATED_WORKER_ID.set(worker_id)
            try:
                async for response in original.stream_stream(request_iterator, context):
                    yield response
            finally:
                _AUTHENTICATED_WORKER_ID.reset(token)

        return grpc.stream_stream_rpc_method_handler(
            wrapped,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )

    @staticmethod
    def _check_mtls_binding(
        context: grpc.aio.ServicerContext,
        authenticated_worker_id: str,
        *,
        require_certificate: bool = False,
    ) -> tuple[bool, str]:
        """从 context.auth_context() 读 peer CN/SAN,和 metadata worker_id 对齐。

        返回 (ok, reason)。
        - 没有 declared_worker_id (不是 worker 发起的请求): 放行,由上层业务校验。
        - 没有 auth_context 或没有 peer 身份 (开发环境无 mTLS): 放行。
        - 有 peer 身份但与 worker_id 不一致: 拒绝。
        """
        if not authenticated_worker_id:
            return True, ""
        identities, read_error = AuthInterceptor._read_peer_identities(context)
        if read_error:
            return False, read_error
        if not identities:
            if require_certificate:
                return False, "mTLS 认证未提供客户端证书身份"
            return True, ""
        if AuthInterceptor._identity_matches_worker(identities, authenticated_worker_id):
            return True, ""
        return False, "mTLS 证书身份与 worker_id 不匹配"

    @staticmethod
    def _read_peer_identities(context: grpc.aio.ServicerContext) -> tuple[list[str], str]:
        try:
            auth_ctx = context.auth_context() or {}
        except Exception:  # noqa: BLE001 - 不应阻断主流程,但要拒绝
            return [], "无法读取 mTLS auth_context"
        return AuthInterceptor._peer_identities(auth_ctx), ""

    @staticmethod
    def _identity_matches_worker(identities: list[str], authenticated_worker_id: str) -> bool:
        """精确匹配证书身份（P2 §4.2：禁止宽松后缀匹配）。

        允许的形态：
        - CN/SAN 完整相等: ``<worker_id>``
        - 前缀标记整串相等: ``worker:<worker_id>``
        - URI SAN: 解析后 path 最后一段精确等于 ``<worker_id>``。
        此前 ``endswith("worker:<id>")`` 会被 ``evilworker:<id>`` 这类
        拼接身份绕过。
        """
        from urllib.parse import urlsplit

        for identity in identities:
            if identity == authenticated_worker_id:
                return True
            if identity == f"worker:{authenticated_worker_id}":
                return True
            if "://" in identity:
                segments = [segment for segment in urlsplit(identity).path.split("/") if segment]
                if segments and segments[-1] == authenticated_worker_id:
                    return True
        return False

    @staticmethod
    def _peer_identities(auth_context: dict) -> list[str]:
        identities: list[str] = []
        keys = ("x509_common_name", "x509_subject_alternative_name")
        for key in keys:
            for raw in auth_context.get(key, []) or []:
                value = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                identities.append(value)
        return identities
