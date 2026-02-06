"""
认证验证

实现 dispatch 认证验证和可选任务签名验证。

Gateway 模式必须支持 mTLS 或 API key 认证。
可选：任务签名验证（如果 TaskDispatch 包含签名字段）。

Requirements: 11.3
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from loguru import logger


class AuthMethod(Enum):
    """认证方式"""
    NONE = "none"
    API_KEY = "api_key"
    MTLS = "mtls"
    TOKEN = "token"


class VerifyResult(Enum):
    """验证结果"""
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    MISSING_SIGNATURE = "missing_signature"
    DISABLED = "disabled"


@dataclass
class TaskSignature:
    """任务签名"""
    issued_at: int
    expires_at: int
    nonce: str
    signature: str
    algorithm: str = "hmac-sha256"

    def is_expired(self) -> bool:
        return int(time.time()) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "signature": self.signature,
            "algorithm": self.algorithm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["TaskSignature"]:
        try:
            return cls(
                issued_at=data["issued_at"],
                expires_at=data["expires_at"],
                nonce=data["nonce"],
                signature=data["signature"],
                algorithm=data.get("algorithm", "hmac-sha256"),
            )
        except (KeyError, TypeError) as e:
            logger.warning(f"解析任务签名失败: {e}")
            return None


@dataclass
class VerificationContext:
    """验证上下文"""
    task_id: str
    run_id: str | None = None
    payload_hash: str | None = None
    timestamp: int | None = None

    def to_signing_string(self) -> str:
        parts = [self.task_id]
        if self.run_id:
            parts.append(self.run_id)
        if self.payload_hash:
            parts.append(self.payload_hash)
        if self.timestamp:
            parts.append(str(self.timestamp))
        return ":".join(parts)


class Verifier:
    """认证验证器 - Requirements: 11.3"""

    def __init__(
        self,
        secret_key: str | None = None,
        public_key: str | None = None,
        signature_enabled: bool = False,
        clock_skew_seconds: int = 300,
        nonce_cache_size: int = 10000,
    ):
        self._secret_key = secret_key
        self._public_key = public_key
        self._signature_enabled = signature_enabled and (secret_key is not None or public_key is not None)
        self._clock_skew = clock_skew_seconds
        self._nonce_cache_size = nonce_cache_size
        self._used_nonces: dict[str, int] = {}

        if self._signature_enabled:
            logger.info("任务签名验证已启用")
        else:
            logger.debug("任务签名验证未启用")

    def is_enabled(self) -> bool:
        return self._signature_enabled

    def verify_task(
        self,
        task_id: str,
        signature: TaskSignature | None = None,
        context: VerificationContext | None = None,
    ) -> tuple[VerifyResult, str]:
        if not self._signature_enabled:
            return (VerifyResult.DISABLED, "签名验证未启用")

        if not signature:
            return (VerifyResult.MISSING_SIGNATURE, "任务缺少签名")

        now = int(time.time())
        if signature.is_expired():
            return (VerifyResult.EXPIRED, "任务签名已过期")

        if signature.issued_at > now + self._clock_skew:
            return (VerifyResult.FAILED, "任务签名时间异常")

        if not self._check_nonce(signature.nonce, signature.expires_at):
            return (VerifyResult.FAILED, "任务签名 nonce 重复")

        if not self._verify_signature(task_id, signature, context):
            return (VerifyResult.INVALID_SIGNATURE, "任务签名验证失败")

        return (VerifyResult.SUCCESS, "验证成功")

    def _check_nonce(self, nonce: str, expires_at: int) -> bool:
        now = int(time.time())
        self._cleanup_nonces(now)

        if nonce in self._used_nonces:
            return False

        self._used_nonces[nonce] = expires_at

        if len(self._used_nonces) > self._nonce_cache_size:
            oldest = min(self._used_nonces.items(), key=lambda x: x[1])
            del self._used_nonces[oldest[0]]

        return True

    def _cleanup_nonces(self, now: int) -> None:
        expired = [k for k, v in self._used_nonces.items() if v < now]
        for k in expired:
            del self._used_nonces[k]

    def _verify_signature(
        self,
        task_id: str,
        signature: TaskSignature,
        context: VerificationContext | None,
    ) -> bool:
        if signature.algorithm != "hmac-sha256":
            return False

        if not self._secret_key:
            return False

        signing_string = self._build_signing_string(task_id, signature, context)
        expected_signature = self._compute_hmac(signing_string)
        return hmac.compare_digest(expected_signature, signature.signature)

    def _build_signing_string(
        self,
        task_id: str,
        signature: TaskSignature,
        context: VerificationContext | None,
    ) -> str:
        parts = [task_id, str(signature.issued_at), str(signature.expires_at), signature.nonce]
        if context:
            if context.run_id:
                parts.append(context.run_id)
            if context.payload_hash:
                parts.append(context.payload_hash)
        return ":".join(parts)

    def _compute_hmac(self, message: str) -> str:
        if not self._secret_key:
            return ""
        return hmac.new(
            self._secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def create_signature(
        self,
        task_id: str,
        context: VerificationContext | None = None,
        ttl_seconds: int = 3600,
    ) -> TaskSignature | None:
        if not self._secret_key:
            return None

        import secrets

        now = int(time.time())
        nonce = secrets.token_hex(16)

        signature = TaskSignature(
            issued_at=now,
            expires_at=now + ttl_seconds,
            nonce=nonce,
            signature="",
            algorithm="hmac-sha256",
        )

        signing_string = self._build_signing_string(task_id, signature, context)
        signature.signature = self._compute_hmac(signing_string)
        return signature


class DispatchVerifier:
    """Dispatch 认证验证器 - Requirements: 11.3"""

    def __init__(
        self,
        expected_api_key: str | None = None,
        expected_cn: str | None = None,
        require_auth: bool = True,
    ):
        self._expected_api_key = expected_api_key
        self._expected_cn = expected_cn
        self._require_auth = require_auth

        self._available_methods: list[AuthMethod] = []
        if expected_api_key:
            self._available_methods.append(AuthMethod.API_KEY)
        if expected_cn:
            self._available_methods.append(AuthMethod.MTLS)

    def verify_api_key(self, api_key: str | None) -> tuple[bool, str]:
        if not self._expected_api_key:
            if self._require_auth:
                return (False, "未配置 API Key")
            return (True, "API Key 验证已禁用")

        if not api_key:
            return (False, "缺少 API Key")

        if not hmac.compare_digest(api_key, self._expected_api_key):
            return (False, "API Key 无效")

        return (True, "API Key 验证成功")

    def verify_mtls(self, cert_cn: str | None) -> tuple[bool, str]:
        if not self._expected_cn:
            if self._require_auth:
                return (False, "未配置 mTLS CN")
            return (True, "mTLS 验证已禁用")

        if not cert_cn:
            return (False, "缺少证书 CN")

        if cert_cn != self._expected_cn:
            return (False, f"证书 CN 不匹配: {cert_cn}")

        return (True, "mTLS 验证成功")

    def verify(
        self,
        api_key: str | None = None,
        cert_cn: str | None = None,
    ) -> tuple[bool, AuthMethod, str]:
        if not self._require_auth:
            return (True, AuthMethod.NONE, "认证已禁用")

        if AuthMethod.MTLS in self._available_methods and cert_cn:
            success, message = self.verify_mtls(cert_cn)
            if success:
                return (True, AuthMethod.MTLS, message)

        if AuthMethod.API_KEY in self._available_methods and api_key:
            success, message = self.verify_api_key(api_key)
            if success:
                return (True, AuthMethod.API_KEY, message)

        if not self._available_methods:
            return (False, AuthMethod.NONE, "未配置任何认证方式")

        return (False, AuthMethod.NONE, "认证失败")

    @property
    def available_methods(self) -> list[AuthMethod]:
        return self._available_methods.copy()

    @property
    def require_auth(self) -> bool:
        return self._require_auth


# 全局验证器实例
_task_verifier: Verifier | None = None
_dispatch_verifier: DispatchVerifier | None = None


def get_task_verifier() -> Verifier | None:
    return _task_verifier


def set_task_verifier(verifier: Verifier) -> None:
    global _task_verifier
    _task_verifier = verifier


def get_dispatch_verifier() -> DispatchVerifier | None:
    return _dispatch_verifier


def set_dispatch_verifier(verifier: DispatchVerifier) -> None:
    global _dispatch_verifier
    _dispatch_verifier = verifier


def init_verifiers(
    secret_key: str | None = None,
    public_key: str | None = None,
    signature_enabled: bool = False,
    expected_api_key: str | None = None,
    expected_cn: str | None = None,
    require_auth: bool = True,
) -> tuple[Verifier, DispatchVerifier]:
    task_verifier = Verifier(
        secret_key=secret_key,
        public_key=public_key,
        signature_enabled=signature_enabled,
    )
    set_task_verifier(task_verifier)

    dispatch_verifier = DispatchVerifier(
        expected_api_key=expected_api_key,
        expected_cn=expected_cn,
        require_auth=require_auth,
    )
    set_dispatch_verifier(dispatch_verifier)

    logger.info(
        f"验证器已初始化: "
        f"signature_enabled={signature_enabled}, "
        f"require_auth={require_auth}, "
        f"methods={[m.value for m in dispatch_verifier.available_methods]}"
    )

    return (task_verifier, dispatch_verifier)
