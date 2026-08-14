"""API Key / JWT 凭据校验。

所有分支一律 fail-closed:安全模块不可用、校验器抛异常、身份与声明不匹配
都返回失败的 ``AuthResult``,不存在"环境降级就放行"的兜底路径(P0-#4)。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from loguru import logger

from antcode_gateway.auth_principal import AuthResult
from antcode_gateway.security_audit import EVENT_API_KEY_INVALID, EVENT_JWT_INVALID, EVENT_MTLS_REJECT

# 凭据所在的 gRPC metadata 键名（grpc 元数据按 HTTP/2 习惯全小写）
API_KEY_HEADER = "x-api-key"
WORKER_ID_HEADER = "x-worker-id"
AUTHORIZATION_HEADER = "authorization"
BEARER_PREFIX = "bearer "

# 失败原因文案（Register 链路也复用 INVALID_API_KEY_ERROR 做审计事件分类）
INVALID_API_KEY_ERROR = "无效的 API Key"
API_KEY_WORKER_MISMATCH_ERROR = "API Key 与 Worker 身份不匹配"
SECURITY_MODULE_UNAVAILABLE_ERROR = "security_module_unavailable"


def presented_credential_event(metadata: dict) -> str:
    """按请求实际携带的凭据类型给认证失败归类，供安全审计使用。"""
    if metadata.get(API_KEY_HEADER):
        return EVENT_API_KEY_INVALID
    if str(metadata.get(AUTHORIZATION_HEADER) or "").lower().startswith(BEARER_PREFIX):
        return EVENT_JWT_INVALID
    return EVENT_MTLS_REJECT


async def authenticate_api_key(
    api_key: str,
    worker_id: str | None,
    validator: Callable[..., Any] | None,
) -> AuthResult:
    """API Key 认证。

    P0-a1: 严格要求 worker_id 参与绑定,不再对无 worker_id 的调用做兜底。
    上层 ``AuthInterceptor._authenticate`` 已在 API Key 分支预先校验 worker_id
    非空,这里二次校验(直接绕过入口调用本函数的路径也不会失守)。
    """
    if not api_key:
        return AuthResult(success=False, error="API Key 为空")
    if not worker_id:
        return AuthResult(success=False, error="API Key 认证缺少 X-Worker-ID")
    if validator is not None:
        return await _authenticate_custom_api_key(api_key, worker_id, validator)
    return await _authenticate_default_api_key(api_key, worker_id)


async def _authenticate_custom_api_key(api_key: str, worker_id: str, validator: Callable[..., Any]) -> AuthResult:
    try:
        result, bound_worker = await _call_api_key_validator(api_key, worker_id, validator)
        return normalize_api_key_result(result, worker_id, bound_worker)
    except Exception as exc:
        logger.exception(f"API Key 验证异常: {exc}")
        return AuthResult(success=False, error="API Key 验证失败")


async def _authenticate_default_api_key(api_key: str, worker_id: str) -> AuthResult:
    try:
        from antcode_core.common.security import verify_api_key

        valid = await verify_api_key(api_key, worker_id)
        return AuthResult(
            success=valid,
            worker_id=worker_id if valid else None,
            auth_method="api_key" if valid else None,
            error=None if valid else INVALID_API_KEY_ERROR,
        )
    except ImportError as exc:
        logger.exception(f"安全模块不可用,拒绝认证: {exc}")
        return AuthResult(success=False, error=SECURITY_MODULE_UNAVAILABLE_ERROR)
    except Exception as exc:
        logger.exception(f"API Key 验证异常: {exc}")
        return AuthResult(success=False, error="API Key 验证失败")


async def _call_api_key_validator(
    api_key: str,
    worker_id: str,
    validator: Callable[..., Any],
) -> tuple[Any, bool]:
    """调用自定义校验器,返回 (结果, 校验器是否接收了 worker_id)。"""
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


def normalize_api_key_result(result: Any, declared_worker_id: str, validator_bound_worker: bool) -> AuthResult:
    """把自定义校验器的多种返回形态归一成 ``AuthResult``。"""
    if isinstance(result, AuthResult):
        return _normalize_auth_result(result, declared_worker_id)
    if isinstance(result, dict):
        return _bind_or_mismatch(result.get("worker_id") or result.get("sub"), declared_worker_id)
    if isinstance(result, str):
        return _bind_or_mismatch(result, declared_worker_id)
    if isinstance(result, bool) and result:
        return _normalize_bool_result(declared_worker_id, validator_bound_worker)
    return AuthResult(success=False, error=INVALID_API_KEY_ERROR)


def _normalize_auth_result(result: AuthResult, declared_worker_id: str) -> AuthResult:
    if result.success and result.worker_id == declared_worker_id:
        return result
    return AuthResult(success=False, error=API_KEY_WORKER_MISMATCH_ERROR)


def _normalize_bool_result(declared_worker_id: str, validator_bound_worker: bool) -> AuthResult:
    if validator_bound_worker:
        return AuthResult(True, declared_worker_id, auth_method="api_key")
    return AuthResult(success=False, error="自定义 API Key validator 必须绑定 Worker 身份")


def _bind_or_mismatch(identity: Any, declared_worker_id: str) -> AuthResult:
    if identity == declared_worker_id:
        return AuthResult(True, declared_worker_id, auth_method="api_key")
    return AuthResult(success=False, error=API_KEY_WORKER_MISMATCH_ERROR)


async def authenticate_jwt(token: str, validator: Callable[[str], dict | None] | None) -> AuthResult:
    """JWT 认证（只接受 Worker 专用 token_class）。"""
    if not token:
        return AuthResult(success=False, error="JWT token 为空")
    if validator is not None:
        return _authenticate_custom_jwt(token, validator)
    return _authenticate_default_jwt(token)


def _authenticate_custom_jwt(token: str, validator: Callable[[str], dict | None]) -> AuthResult:
    try:
        payload = validator(token)
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


def _authenticate_default_jwt(token: str) -> AuthResult:
    try:
        from antcode_core.common.security.auth import jwt_auth

        try:
            token_data = jwt_auth.verify_token(token, expected_type="access", expected_class="worker")
        except Exception as verify_exc:  # noqa: BLE001 - HTTPException / AuthError 都归为无效
            logger.warning(f"Worker JWT 校验失败: {verify_exc}")
            return AuthResult(success=False, error="无效的 Worker JWT token")

        # Worker token 里 username 已由 verify_token 设为 worker_id
        worker_id = getattr(token_data, "username", None)
        if not worker_id:
            return AuthResult(success=False, error="Worker JWT 缺少 worker_id")
        return AuthResult(success=True, worker_id=worker_id, auth_method="jwt")
    except ImportError as exc:
        logger.exception(f"安全模块不可用,拒绝认证: {exc}")
        return AuthResult(success=False, error=SECURITY_MODULE_UNAVAILABLE_ERROR)
    except Exception as exc:
        logger.exception(f"JWT 验证异常: {exc}")
        return AuthResult(success=False, error="JWT 验证失败")


__all__ = [
    "API_KEY_HEADER",
    "API_KEY_WORKER_MISMATCH_ERROR",
    "AUTHORIZATION_HEADER",
    "BEARER_PREFIX",
    "INVALID_API_KEY_ERROR",
    "SECURITY_MODULE_UNAVAILABLE_ERROR",
    "WORKER_ID_HEADER",
    "authenticate_api_key",
    "authenticate_jwt",
    "normalize_api_key_result",
    "presented_credential_event",
]
