"""
Gateway 认证模块

实现 mTLS / API key 认证机制。

Requirements: 5.5
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class AuthMethod(str, Enum):
    """认证方法"""

    NONE = "none"          # 无认证（仅用于测试）
    API_KEY = "api_key"    # API Key 认证
    MTLS = "mtls"          # mTLS 双向认证
    JWT = "jwt"            # JWT Token 认证
    HMAC = "hmac"          # HMAC 签名认证


@dataclass
class AuthConfig:
    """认证配置"""

    method: AuthMethod = AuthMethod.API_KEY

    # API Key 认证
    api_key: str | None = None
    api_key_header: str = "x-api-key"

    # Worker 身份
    worker_id: str | None = None
    worker_id_header: str = "x-worker-id"

    # mTLS 认证
    client_cert_path: str | None = None
    client_key_path: str | None = None
    ca_cert_path: str | None = None

    # JWT 认证
    jwt_token: str | None = None
    jwt_header: str = "authorization"
    jwt_prefix: str = "Bearer"

    # HMAC 认证
    hmac_secret: str | None = None
    hmac_algorithm: str = "sha256"
    hmac_header: str = "x-signature"
    hmac_timestamp_header: str = "x-timestamp"
    hmac_nonce_header: str = "x-nonce"
    hmac_tolerance_seconds: int = 300  # 5 分钟时间容差

    # 额外元数据
    extra_metadata: dict[str, str] = field(default_factory=dict)


class GatewayAuthenticator:
    """
    Gateway 认证器

    支持多种认证方式：
    - API Key：简单的密钥认证
    - mTLS：双向 TLS 证书认证
    - JWT：JSON Web Token 认证
    - HMAC：基于签名的认证

    Requirements: 5.5
    """

    def __init__(self, config: AuthConfig):
        self._config = config
        self._cached_metadata: list[tuple[str, str]] | None = None
        self._metadata_timestamp: float = 0

        # 验证配置
        self._validate_config()

    def _validate_config(self) -> None:
        """验证认证配置"""
        method = self._config.method

        if method == AuthMethod.API_KEY:
            if not self._config.api_key:
                logger.warning("API Key 认证已启用但未配置 api_key")

        elif method == AuthMethod.MTLS:
            if not self._config.client_cert_path or not self._config.client_key_path:
                raise ValueError("mTLS 认证需要配置 client_cert_path 和 client_key_path")

            # 验证证书文件存在
            cert_path = Path(self._config.client_cert_path)
            key_path = Path(self._config.client_key_path)

            if not cert_path.exists():
                raise ValueError(f"客户端证书不存在: {cert_path}")
            if not key_path.exists():
                raise ValueError(f"客户端密钥不存在: {key_path}")

        elif method == AuthMethod.JWT:
            if not self._config.jwt_token:
                logger.warning("JWT 认证已启用但未配置 jwt_token")

        elif method == AuthMethod.HMAC and not self._config.hmac_secret:
            raise ValueError("HMAC 认证需要配置 hmac_secret")

    def get_metadata(self) -> list[tuple[str, str]]:
        """
        获取认证元数据

        返回用于 gRPC 调用的元数据列表。
        """
        method = self._config.method

        if method == AuthMethod.NONE:
            return self._get_base_metadata()

        elif method == AuthMethod.API_KEY:
            return self._get_api_key_metadata()

        elif method == AuthMethod.MTLS:
            # mTLS 认证通过 TLS 握手完成，不需要额外元数据
            return self._get_base_metadata()

        elif method == AuthMethod.JWT:
            return self._get_jwt_metadata()

        elif method == AuthMethod.HMAC:
            return self._get_hmac_metadata()

        else:
            logger.warning(f"未知的认证方法: {method}")
            return self._get_base_metadata()

    def _get_base_metadata(self) -> list[tuple[str, str]]:
        """获取基础元数据"""
        metadata = []

        # 添加 worker_id
        if self._config.worker_id:
            metadata.append((self._config.worker_id_header, self._config.worker_id))

        # 添加额外元数据
        for key, value in self._config.extra_metadata.items():
            metadata.append((key, value))

        return metadata

    def _get_api_key_metadata(self) -> list[tuple[str, str]]:
        """获取 API Key 认证元数据"""
        metadata = self._get_base_metadata()

        if self._config.api_key:
            metadata.append((self._config.api_key_header, self._config.api_key))

        return metadata

    def _get_jwt_metadata(self) -> list[tuple[str, str]]:
        """获取 JWT 认证元数据"""
        metadata = self._get_base_metadata()

        if self._config.jwt_token:
            token_value = f"{self._config.jwt_prefix} {self._config.jwt_token}"
            metadata.append((self._config.jwt_header, token_value))

        return metadata

    def _get_hmac_metadata(self) -> list[tuple[str, str]]:
        """获取 HMAC 认证元数据"""
        metadata = self._get_base_metadata()

        if not self._config.hmac_secret:
            return metadata

        # 生成时间戳和 nonce
        timestamp = str(int(time.time()))
        nonce = self._generate_nonce()

        # 构建签名字符串
        sign_string = self._build_sign_string(timestamp, nonce)

        # 计算签名
        signature = self._compute_hmac_signature(sign_string)

        # 添加认证头
        metadata.append((self._config.hmac_timestamp_header, timestamp))
        metadata.append((self._config.hmac_nonce_header, nonce))
        metadata.append((self._config.hmac_header, signature))

        return metadata

    def _generate_nonce(self) -> str:
        """生成随机 nonce"""
        import secrets
        return secrets.token_hex(16)

    def _build_sign_string(self, timestamp: str, nonce: str) -> str:
        """构建签名字符串"""
        parts = [
            self._config.worker_id or "",
            timestamp,
            nonce,
        ]
        return "\n".join(parts)

    def _compute_hmac_signature(self, message: str) -> str:
        """计算 HMAC 签名"""
        if not self._config.hmac_secret:
            return ""

        algorithm = self._config.hmac_algorithm.lower()

        if algorithm == "sha256":
            hash_func = hashlib.sha256
        elif algorithm == "sha384":
            hash_func = hashlib.sha384
        elif algorithm == "sha512":
            hash_func = hashlib.sha512
        else:
            hash_func = hashlib.sha256

        signature = hmac.new(
            self._config.hmac_secret.encode("utf-8"),
            message.encode("utf-8"),
            hash_func,
        ).hexdigest()

        return signature

    def get_tls_credentials(self) -> tuple[bytes | None, bytes | None, bytes | None]:
        """
        获取 TLS 凭证

        Returns:
            (root_certificates, private_key, certificate_chain)
        """
        root_certs = None
        private_key = None
        certificate_chain = None

        if self._config.ca_cert_path:
            ca_path = Path(self._config.ca_cert_path)
            if ca_path.exists():
                root_certs = ca_path.read_bytes()

        if self._config.client_cert_path:
            cert_path = Path(self._config.client_cert_path)
            if cert_path.exists():
                certificate_chain = cert_path.read_bytes()

        if self._config.client_key_path:
            key_path = Path(self._config.client_key_path)
            if key_path.exists():
                private_key = key_path.read_bytes()

        return root_certs, private_key, certificate_chain

    def update_credentials(
        self,
        api_key: str | None = None,
        jwt_token: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        """
        更新凭证

        支持运行时更新凭证，无需重启。
        """
        if api_key is not None:
            self._config.api_key = api_key

        if jwt_token is not None:
            self._config.jwt_token = jwt_token

        if worker_id is not None:
            self._config.worker_id = worker_id

        # 清除缓存
        self._cached_metadata = None

        logger.info("认证凭证已更新")

    def verify_server_response(
        self,
        response_signature: str | None,
        response_body: bytes,
        timestamp: str | None = None,
    ) -> bool:
        """
        验证服务器响应签名（可选）

        用于双向签名验证场景。
        """
        if not response_signature or not self._config.hmac_secret:
            return True  # 未启用响应验证

        # 验证时间戳
        if timestamp:
            try:
                ts = int(timestamp)
                now = int(time.time())
                if abs(now - ts) > self._config.hmac_tolerance_seconds:
                    logger.warning("响应时间戳超出容差范围")
                    return False
            except ValueError:
                logger.warning("无效的响应时间戳")
                return False

        # 验证签名
        expected_signature = self._compute_hmac_signature(response_body.decode("utf-8"))
        return hmac.compare_digest(response_signature, expected_signature)

    @property
    def method(self) -> AuthMethod:
        """获取认证方法"""
        return self._config.method

    @property
    def worker_id(self) -> str | None:
        """获取 Worker ID"""
        return self._config.worker_id

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于调试，不包含敏感信息）"""
        return {
            "method": self._config.method.value,
            "worker_id": self._config.worker_id,
            "has_api_key": bool(self._config.api_key),
            "has_jwt_token": bool(self._config.jwt_token),
            "has_hmac_secret": bool(self._config.hmac_secret),
            "has_client_cert": bool(self._config.client_cert_path),
        }


class AuthInterceptor:
    """
    gRPC 认证拦截器

    自动为所有 gRPC 调用添加认证元数据。
    """

    def __init__(self, authenticator: GatewayAuthenticator):
        self._authenticator = authenticator

    def intercept_unary_unary(
        self,
        continuation: Any,
        client_call_details: Any,
        request: Any,
    ) -> Any:
        """拦截一元调用"""
        new_details = self._add_auth_metadata(client_call_details)
        return continuation(new_details, request)

    def intercept_unary_stream(
        self,
        continuation: Any,
        client_call_details: Any,
        request: Any,
    ) -> Any:
        """拦截一元流调用"""
        new_details = self._add_auth_metadata(client_call_details)
        return continuation(new_details, request)

    def intercept_stream_unary(
        self,
        continuation: Any,
        client_call_details: Any,
        request_iterator: Any,
    ) -> Any:
        """拦截流一元调用"""
        new_details = self._add_auth_metadata(client_call_details)
        return continuation(new_details, request_iterator)

    def intercept_stream_stream(
        self,
        continuation: Any,
        client_call_details: Any,
        request_iterator: Any,
    ) -> Any:
        """拦截流流调用"""
        new_details = self._add_auth_metadata(client_call_details)
        return continuation(new_details, request_iterator)

    def _add_auth_metadata(self, client_call_details: Any) -> Any:
        """添加认证元数据"""
        auth_metadata = self._authenticator.get_metadata()

        # 合并现有元数据
        existing_metadata = client_call_details.metadata or []
        new_metadata = list(existing_metadata) + auth_metadata

        # 创建新的 call details
        return _ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=new_metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
            compression=getattr(client_call_details, "compression", None),
        )


@dataclass
class _ClientCallDetails:
    """gRPC 调用详情"""

    method: str
    timeout: float | None
    metadata: list[tuple[str, str]] | None
    credentials: Any
    wait_for_ready: bool | None
    compression: Any = None
