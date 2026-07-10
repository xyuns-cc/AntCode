"""
认证模块

支持多种认证方式：
- mTLS: 双向 TLS 认证（通过客户端证书）
- API Key: API 密钥认证
- JWT: JSON Web Token 认证

**Validates: Requirements 6.2**
"""

import time
from collections.abc import Callable
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
    AUTH_EXEMPT_METHODS = frozenset([
        "/antcode.v1.ControlService/Register",      # 首次注册（此时无 api_key）
        "/grpc.health.v1.Health/Check",             # gRPC 健康检查
        "/grpc.health.v1.Health/Watch",
    ])

    # 兼容旧命名（仅本模块内部曾用；保留避免误伤外部引用）
    SKIP_AUTH_METHODS = AUTH_EXEMPT_METHODS

    def __init__(
        self,
        enabled: bool = True,
        api_key_validator: Callable[[str], bool] | None = None,
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
            return await continuation(handler_call_details)

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
            has_api_key = bool(metadata.get(self.API_KEY_HEADER))
            # P2-#28: worker_id 写日志前先 strip 换行 / 截断长度,防止日志注入
            worker_id = self._sanitize_worker_id(metadata.get(self.WORKER_ID_HEADER))
            logger.warning(
                f"认证失败: {auth_result.error}, method={method}, "
                f"has_api_key={has_api_key}, worker_id={worker_id}"
            )

            # 追加结构化安全审计（不影响主流程）
            auth_header = metadata.get(self.AUTHORIZATION_HEADER) or ""
            has_jwt = auth_header.lower().startswith("bearer ")
            if has_api_key:
                event_type = "api_key_invalid"
            elif has_jwt:
                event_type = "jwt_invalid"
            else:
                # 没有任何认证凭证：归类为 mTLS 拒绝 / 凭证缺失
                event_type = "mtls_reject"
            peer = metadata.get("x-forwarded-for") or metadata.get("x-real-ip") or ""
            # P5.4: 如果 worker 在 metadata 里带了 W3C traceparent 头,
            # 把 trace_id 一起记入审计事件,方便事后跨服务追查"为什么
            # 这次请求被拒"。无 traceparent 时 trace_id=None,审计字段
            # 自动省略,不影响兼容性。
            trace_id = _extract_trace_id_from_metadata(metadata)
            await self._emit_audit(
                event_type=event_type,
                worker_id=worker_id,
                peer=peer,
                reason=auth_result.error or "",
                trace_id=trace_id,
            )

            return await self._build_reject_handler(
                continuation, handler_call_details, auth_result.error or ""
            )

        logger.debug(
            f"认证成功: worker_id={auth_result.worker_id}, "
            f"method={auth_result.auth_method}"
        )

        # 认证成功:对开启 mTLS 的链路再做 CN/SAN <-> worker_id 绑定校验。
        # 由于 intercept_service 阶段拿不到 ServicerContext,这里 wrap 一层
        # handler,在实际 RPC 调用时校验 peer 证书。
        declared_worker_id = metadata.get(self.WORKER_ID_HEADER) or ""
        return await self._wrap_with_mtls_check(
            continuation=continuation,
            handler_call_details=handler_call_details,
            declared_worker_id=declared_worker_id,
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

        # 3. mTLS: 实际的 CN/SAN 绑定校验在 _wrap_with_mtls_check 里, 拿到
        # ServicerContext 后从 ``auth_context()`` 取 peer 证书身份, 与
        # metadata 中的 worker_id 强一致。这里只负责"未提供任何 API Key
        # / JWT 凭证"的兜底拒绝。

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
            try:
                # 自定义验证器返回 True 时仍严格绑定客户端声明的 worker_id
                is_valid = self._api_key_validator(api_key)
                if is_valid:
                    return AuthResult(
                        success=True,
                        worker_id=worker_id,
                        auth_method="api_key",
                    )
                return AuthResult(success=False, error="无效的 API Key")
            except Exception as e:
                logger.exception(f"API Key 验证异常: {e}")
                return AuthResult(success=False, error="API Key 验证失败")

        # 默认验证:调 antcode_core 的 verify_api_key,它会把 api_key 与
        # public_id=worker_id 联合过滤,严格绑定
        try:
            from antcode_core.common.security import verify_api_key

            is_valid = await verify_api_key(api_key, worker_id)
            if is_valid:
                return AuthResult(
                    success=True,
                    worker_id=worker_id,
                    auth_method="api_key",
                )
            return AuthResult(success=False, error="无效的 API Key")
        except ImportError as e:
            # fail-closed: 安全模块不可用一律拒绝，绝不退回"格式正确即通过"
            logger.exception(f"安全模块不可用,拒绝认证: {e}")
            return AuthResult(success=False, error="security_module_unavailable")
        except Exception as e:
            logger.exception(f"API Key 验证异常: {e}")
            return AuthResult(success=False, error="API Key 验证失败")

    async def _authenticate_jwt(self, token: str) -> AuthResult:
        """JWT 认证"""
        if not token:
            return AuthResult(success=False, error="JWT token 为空")

        # 使用自定义验证器
        if self._jwt_validator:
            try:
                payload = self._jwt_validator(token)
                if payload:
                    worker_id = payload.get("sub") or payload.get("worker_id")
                    return AuthResult(
                        success=True,
                        worker_id=worker_id,
                        auth_method="jwt",
                    )
                return AuthResult(success=False, error="无效的 JWT token")
            except Exception as e:
                logger.exception(f"JWT 验证异常: {e}")
                return AuthResult(success=False, error="JWT 验证失败")

        # 默认验证:调 antcode_core JWTAuth.verify_token,**强制 expected_class="worker"**。
        # P0-a1: 只接受 payload token_class="worker" 的 JWT;普通 Web access token 会被拒。
        # 这样任何"拿 Web JWT 冒充 Worker"的调用都在 JWT 层就拒掉,不到 mTLS 兜底。
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
        except ImportError as e:
            # fail-closed: 安全模块不可用一律拒绝，绝不退回"格式正确即通过"
            logger.exception(f"安全模块不可用,拒绝认证: {e}")
            return AuthResult(success=False, error="security_module_unavailable")
        except Exception as e:
            logger.exception(f"JWT 验证异常: {e}")
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
        declared_worker_id: str,
    ) -> grpc.RpcMethodHandler:
        """P1-#12: 在 mTLS 链路上,确保 peer 证书 CN/SAN 与 metadata 中的
        worker_id 一致。开发环境(无客户端证书)直接放行,生产环境(挂了
        require_client_auth=True)证书必现,不一致即 PERMISSION_DENIED。
        """
        original = await continuation(handler_call_details)
        return self._make_mtls_wrapped_handler(original, declared_worker_id)

    def _make_mtls_wrapped_handler(
        self,
        original: grpc.RpcMethodHandler,
        declared_worker_id: str,
    ) -> grpc.RpcMethodHandler:
        check = self._check_mtls_binding

        async def _abort_pd(context: grpc.aio.ServicerContext, reason: str) -> None:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, reason)

        async def wrapped_unary_unary(request, context):
            ok, reason = check(context, declared_worker_id)
            if not ok:
                await _abort_pd(context, reason)
            return await original.unary_unary(request, context)

        async def wrapped_unary_stream(request, context):
            ok, reason = check(context, declared_worker_id)
            if not ok:
                await _abort_pd(context, reason)
                return
            async for resp in original.unary_stream(request, context):
                yield resp

        async def wrapped_stream_unary(request_iterator, context):
            ok, reason = check(context, declared_worker_id)
            if not ok:
                await _abort_pd(context, reason)
            return await original.stream_unary(request_iterator, context)

        async def wrapped_stream_stream(request_iterator, context):
            ok, reason = check(context, declared_worker_id)
            if not ok:
                await _abort_pd(context, reason)
                return
            async for resp in original.stream_stream(request_iterator, context):
                yield resp

        if original.unary_unary is not None:
            return grpc.unary_unary_rpc_method_handler(
                wrapped_unary_unary,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        if original.unary_stream is not None:
            return grpc.unary_stream_rpc_method_handler(
                wrapped_unary_stream,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        if original.stream_unary is not None:
            return grpc.stream_unary_rpc_method_handler(
                wrapped_stream_unary,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        if original.stream_stream is not None:
            return grpc.stream_stream_rpc_method_handler(
                wrapped_stream_stream,
                request_deserializer=original.request_deserializer,
                response_serializer=original.response_serializer,
            )
        return original

    @staticmethod
    def _check_mtls_binding(
        context: grpc.aio.ServicerContext,
        declared_worker_id: str,
    ) -> tuple[bool, str]:
        """从 context.auth_context() 读 peer CN/SAN,和 metadata worker_id 对齐。

        返回 (ok, reason)。
        - 没有 declared_worker_id (不是 worker 发起的请求): 放行,由上层业务校验。
        - 没有 auth_context 或没有 peer 身份 (开发环境无 mTLS): 放行。
        - 有 peer 身份但与 worker_id 不一致: 拒绝。
        """
        if not declared_worker_id:
            return True, ""
        try:
            auth_ctx = context.auth_context() or {}
        except Exception:  # noqa: BLE001 - 不应阻断主流程,但要拒绝
            return False, "无法读取 mTLS auth_context"

        # gRPC 把对端证书身份放在 x509_common_name / x509_subject_alternative_name
        identities: list[str] = []
        for key in (
            "x509_common_name",
            "x509_subject_alternative_name",
        ):
            for raw in auth_ctx.get(key, []) or []:
                if isinstance(raw, bytes):
                    try:
                        identities.append(raw.decode("utf-8", errors="ignore"))
                    except Exception:  # noqa: BLE001
                        continue
                else:
                    identities.append(str(raw))

        # 没拿到任何证书身份: 视为未开启 mTLS,放行(开发环境兼容)。
        if not identities:
            return True, ""

        if declared_worker_id in identities:
            return True, ""

        # 也兼容 SAN 里挂了 "worker:<id>" / URI 形式
        suffixes = {f"worker:{declared_worker_id}", f"/{declared_worker_id}"}
        for ident in identities:
            if any(ident.endswith(s) for s in suffixes):
                return True, ""

        return False, "mTLS 证书身份与 worker_id 不匹配"
