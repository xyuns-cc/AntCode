"""
Gateway 认证模块测试

测试 API Key 和 JWT 认证功能。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from antcode_gateway.auth import AuthInterceptor, AuthResult


class TestAuthResult:
    """AuthResult 数据类测试"""

    def test_success_result(self):
        result = AuthResult(success=True, worker_id="worker-001", auth_method="api_key")
        assert result.success is True
        assert result.worker_id == "worker-001"
        assert result.auth_method == "api_key"
        assert result.error is None

    def test_failure_result(self):
        result = AuthResult(success=False, error="无效的 API Key")
        assert result.success is False
        assert result.error == "无效的 API Key"
        assert result.worker_id is None


class TestAuthInterceptor:
    """AuthInterceptor 测试"""

    @pytest.fixture
    def interceptor(self):
        return AuthInterceptor(enabled=True)

    @pytest.fixture
    def disabled_interceptor(self):
        return AuthInterceptor(enabled=False)

    @pytest.fixture
    def custom_validator_interceptor(self):
        def api_key_validator(key: str) -> bool:
            return key == "valid_key"

        def jwt_validator(token: str) -> dict | None:
            if token == "valid_token":
                return {"sub": "worker-jwt-001"}
            return None

        return AuthInterceptor(
            enabled=True,
            api_key_validator=api_key_validator,
            jwt_validator=jwt_validator,
        )

    @pytest.mark.asyncio
    async def test_disabled_interceptor_passes_through(self, disabled_interceptor):
        """禁用认证时直接通过"""
        continuation = AsyncMock(return_value="handler")
        handler_details = MagicMock()
        handler_details.method = "/antcode.v1.GatewayService/SendHeartbeat"
        handler_details.invocation_metadata = []

        result = await disabled_interceptor.intercept_service(continuation, handler_details)

        assert result == "handler"
        continuation.assert_called_once_with(handler_details)

    @pytest.mark.asyncio
    async def test_skip_auth_for_health_check(self, interceptor):
        """健康检查跳过认证"""
        continuation = AsyncMock(return_value="handler")
        handler_details = MagicMock()
        handler_details.method = "/grpc.health.v1.Health/Check"
        handler_details.invocation_metadata = []

        result = await interceptor.intercept_service(continuation, handler_details)

        assert result == "handler"
        continuation.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_auth_for_register(self, interceptor):
        """注册接口跳过认证"""
        continuation = AsyncMock(return_value="handler")
        handler_details = MagicMock()
        handler_details.method = "/antcode.v1.GatewayService/Register"
        handler_details.invocation_metadata = []

        result = await interceptor.intercept_service(continuation, handler_details)

        assert result == "handler"
        continuation.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_key_auth_with_custom_validator(self, custom_validator_interceptor):
        """使用自定义验证器的 API Key 认证"""

        result = await custom_validator_interceptor._authenticate_api_key(
            "valid_key", "worker-001"
        )

        assert result.success is True
        assert result.worker_id == "worker-001"
        assert result.auth_method == "api_key"

    @pytest.mark.asyncio
    async def test_api_key_auth_invalid_with_custom_validator(self, custom_validator_interceptor):
        """自定义验证器拒绝无效 API Key"""
        result = await custom_validator_interceptor._authenticate_api_key(
            "invalid_key", "worker-001"
        )

        assert result.success is False
        assert "无效" in result.error

    @pytest.mark.asyncio
    async def test_jwt_auth_with_custom_validator(self, custom_validator_interceptor):
        """使用自定义验证器的 JWT 认证"""
        result = await custom_validator_interceptor._authenticate_jwt("valid_token")

        assert result.success is True
        assert result.worker_id == "worker-jwt-001"
        assert result.auth_method == "jwt"

    @pytest.mark.asyncio
    async def test_jwt_auth_invalid_with_custom_validator(self, custom_validator_interceptor):
        """自定义验证器拒绝无效 JWT"""
        result = await custom_validator_interceptor._authenticate_jwt("invalid_token")

        assert result.success is False

    @pytest.mark.asyncio
    async def test_api_key_fallback_format_validation(self, interceptor):
        """API Key fallback 格式验证"""
        # 模拟 antcode_core 不可用
        with patch.dict("sys.modules", {"antcode_core.common.security": None}):
            # 有效格式
            result = await interceptor._authenticate_api_key(
                "ak_1234567890abcdef", "worker-001"
            )
            assert result.success is True

            # 无效格式
            result = await interceptor._authenticate_api_key("invalid", "worker-001")
            assert result.success is False

    @pytest.mark.asyncio
    async def test_jwt_fallback_format_validation(self, interceptor):
        """JWT fallback 格式验证"""
        # 模拟 antcode_core 不可用
        with patch.dict("sys.modules", {"antcode_core.common.security": None}):
            # 有效 JWT 格式（三段）
            result = await interceptor._authenticate_jwt("header.payload.signature")
            assert result.success is True

            # 无效格式
            result = await interceptor._authenticate_jwt("invalid")
            assert result.success is False

    @pytest.mark.asyncio
    async def test_authenticate_priority(self, interceptor):
        """认证优先级：API Key > JWT"""
        # 同时提供 API Key 和 JWT，应优先使用 API Key
        with patch.object(
            interceptor, "_authenticate_api_key", new_callable=AsyncMock
        ) as mock_api_key:
            mock_api_key.return_value = AuthResult(
                success=True, worker_id="worker-api", auth_method="api_key"
            )

            metadata = {
                "x-api-key": "ak_test",
                "x-worker-id": "worker-001",
                "authorization": "Bearer jwt_token",
            }

            result = await interceptor._authenticate(metadata)

            assert result.success is True
            assert result.auth_method == "api_key"
            mock_api_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_fallback_to_jwt(self, interceptor):
        """API Key 失败时回退到 JWT"""
        with patch.object(
            interceptor, "_authenticate_api_key", new_callable=AsyncMock
        ) as mock_api_key, patch.object(
            interceptor, "_authenticate_jwt", new_callable=AsyncMock
        ) as mock_jwt:
            mock_api_key.return_value = AuthResult(success=False, error="无效")
            mock_jwt.return_value = AuthResult(
                success=True, worker_id="worker-jwt", auth_method="jwt"
            )

            metadata = {
                "x-api-key": "invalid",
                "authorization": "Bearer valid_token",
            }

            result = await interceptor._authenticate(metadata)

            assert result.success is True
            assert result.auth_method == "jwt"

    @pytest.mark.asyncio
    async def test_authenticate_no_credentials(self, interceptor):
        """无认证信息时失败"""
        result = await interceptor._authenticate({})

        assert result.success is False
        assert "未提供" in result.error

    def test_create_unauthenticated_handler(self, interceptor):
        """创建认证失败处理器"""
        handler = interceptor._create_unauthenticated_handler("测试错误")
        assert handler is not None


class TestAuthInterceptorIntegration:
    """认证拦截器集成测试"""

    @pytest.mark.asyncio
    async def test_full_auth_flow_success(self):
        """完整认证流程 - 成功"""
        interceptor = AuthInterceptor(
            enabled=True,
            api_key_validator=lambda k: k == "ak_valid_key_12345678",
        )

        continuation = AsyncMock(return_value="handler")
        handler_details = MagicMock()
        handler_details.method = "/antcode.v1.GatewayService/SendHeartbeat"
        handler_details.invocation_metadata = [
            ("x-api-key", "ak_valid_key_12345678"),
            ("x-worker-id", "worker-001"),
        ]

        result = await interceptor.intercept_service(continuation, handler_details)

        assert result == "handler"
        continuation.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_auth_flow_failure(self):
        """完整认证流程 - 失败"""
        interceptor = AuthInterceptor(
            enabled=True,
            api_key_validator=lambda k: False,
        )

        continuation = AsyncMock(return_value="handler")
        handler_details = MagicMock()
        handler_details.method = "/antcode.v1.GatewayService/SendHeartbeat"
        handler_details.invocation_metadata = [
            ("x-api-key", "invalid_key"),
        ]

        result = await interceptor.intercept_service(continuation, handler_details)

        # 应返回认证失败处理器，而不是调用 continuation
        continuation.assert_not_called()
        assert result is not None
