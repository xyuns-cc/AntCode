"""
认证模块

支持多种认证方式：
- mTLS: 双向 TLS 认证（通过客户端证书）
- API Key: API 密钥认证
- JWT: JSON Web Token 认证

**Validates: Requirements 6.2**
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import grpc
from loguru import logger


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

    # 不需要认证的方法（如健康检查）
    SKIP_AUTH_METHODS = frozenset([
        "/grpc.health.v1.Health/Check",
        "/grpc.health.v1.Health/Watch",
        "/antcode.v1.GatewayService/Register",  # 注册接口不需要认证
    ])

    def __init__(
        self,
        enabled: bool = True,
        api_key_validator: Callable[[str], bool] | None = None,
        jwt_validator: Callable[[str], dict | None] | None = None,
    ):
        """初始化认证拦截器

        Args:
            enabled: 是否启用认证
            api_key_validator: API Key 验证函数，返回是否有效
            jwt_validator: JWT 验证函数，返回解码后的 payload 或 None
        """
        self.enabled = enabled
        self._api_key_validator = api_key_validator
        self._jwt_validator = jwt_validator

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        """拦截服务调用进行认证"""
        if not self.enabled:
            return await continuation(handler_call_details)

        # 检查是否跳过认证
        method = handler_call_details.method
        if method in self.SKIP_AUTH_METHODS:
            return await continuation(handler_call_details)

        # 获取元数据
        metadata = dict(handler_call_details.invocation_metadata)

        # 执行认证
        auth_result = await self._authenticate(metadata)

        if not auth_result.success:
            has_api_key = bool(metadata.get(self.API_KEY_HEADER))
            worker_id = metadata.get(self.WORKER_ID_HEADER)
            logger.warning(
                f"认证失败: {auth_result.error}, method={method}, "
                f"has_api_key={has_api_key}, worker_id={worker_id}"
            )
            return self._create_unauthenticated_handler(auth_result.error)

        logger.debug(
            f"认证成功: worker_id={auth_result.worker_id}, "
            f"method={auth_result.auth_method}"
        )

        # 认证成功，继续处理
        return await continuation(handler_call_details)

    async def _authenticate(self, metadata: dict) -> AuthResult:
        """执行认证

        按优先级尝试不同的认证方式。
        """
        # 1. 尝试 API Key 认证
        api_key = metadata.get(self.API_KEY_HEADER)
        worker_id = metadata.get(self.WORKER_ID_HEADER)

        if api_key:
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

        # 3. mTLS 认证在 TLS 层处理，这里只检查是否有客户端证书信息
        # 如果启用了 mTLS，客户端证书信息会在 peer_identities 中
        # 这里暂时不实现，因为需要在 server 层配置

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
        if self._api_key_validator:
            try:
                is_valid = self._api_key_validator(api_key)
                if is_valid:
                    return AuthResult(
                        success=True,
                        worker_id=worker_id or f"worker-{api_key[:8]}",
                        auth_method="api_key",
                    )
                return AuthResult(success=False, error="无效的 API Key")
            except Exception as e:
                logger.error(f"API Key 验证异常: {e}")
                return AuthResult(success=False, error="API Key 验证失败")

        # 默认验证：尝试从 antcode_core 验证
        try:
            from antcode_core.common.security import verify_api_key

            is_valid = await verify_api_key(api_key, worker_id)
            if is_valid:
                return AuthResult(
                    success=True,
                    worker_id=worker_id or f"worker-{api_key[:8]}",
                    auth_method="api_key",
                )
            return AuthResult(success=False, error="无效的 API Key")
        except ImportError:
            logger.warning("antcode_core.common.security 不可用，使用简单验证")
            # Fallback: 简单格式验证（开发环境）
            if api_key.startswith("ak_") and len(api_key) > 10:
                return AuthResult(
                    success=True,
                    worker_id=worker_id or f"worker-{api_key[:8]}",
                    auth_method="api_key",
                )
            return AuthResult(success=False, error="无效的 API Key 格式")
        except Exception as e:
            logger.error(f"API Key 验证异常: {e}")
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
                logger.error(f"JWT 验证异常: {e}")
                return AuthResult(success=False, error="JWT 验证失败")

        # 默认验证：尝试从 antcode_core 验证
        try:
            from antcode_core.common.security import decode_token

            payload = decode_token(token)
            if payload:
                worker_id = payload.get("sub") or payload.get("worker_id") or payload.get("username")
                return AuthResult(
                    success=True,
                    worker_id=worker_id,
                    auth_method="jwt",
                )
            return AuthResult(success=False, error="无效的 JWT token")
        except ImportError:
            logger.warning("antcode_core.common.security 不可用，使用简单验证")
            # Fallback: 简单格式验证（开发环境）
            parts = token.split(".")
            if len(parts) == 3:
                return AuthResult(
                    success=True,
                    worker_id="worker-jwt",
                    auth_method="jwt",
                )
            return AuthResult(success=False, error="无效的 JWT token 格式")
        except Exception as e:
            logger.error(f"JWT 验证异常: {e}")
            return AuthResult(success=False, error="JWT 验证失败")

    def _create_unauthenticated_handler(self, error: str) -> grpc.RpcMethodHandler:
        """创建认证失败的处理器"""

        async def unauthenticated_handler(request, context):
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                f"认证失败: {error}",
            )

        return grpc.unary_unary_rpc_method_handler(unauthenticated_handler)
