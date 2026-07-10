"""基础接口"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.users.user_service import user_service
from antcode_core.common.config import settings
from antcode_core.common.security.auth import (
    TokenData,
    get_current_user,
    jwt_auth,
    verify_refresh_token,
)
from antcode_core.common.security.login_crypto import (
    LoginPasswordCryptoError,
    login_password_crypto,
)
from antcode_core.domain.models.user import UserRole
from antcode_core.domain.schemas import (
    AppInfoResponse,
    HealthResponse,
    LoginPublicKeyResponse,
    UserLoginRequest,
    UserLoginResponse,
    UserResponse,
)
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.infrastructure.resilience.health import HealthStatus, health_checker
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from antcode_web_api.middleware.middleware import get_client_ip
from antcode_web_api.response import Messages, success

# K8s probe 单次超时（秒）：任何依赖探测卡住不能拖垮探针
_PROBE_TIMEOUT = 5.0

router = APIRouter()

# 企业内网部署：关闭自助找回/重置密码与公开可用性枚举接口
AUTH_SELF_SERVICE_DISABLED_DETAIL = "企业内部系统已禁用自助认证接口，请联系系统管理员"


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class ProbeStatusResponse(BaseModel):
    status: str
    timestamp: str


@router.get(
    "/health",
    response_model=BaseResponse[HealthResponse],
    summary="健康检查",
    tags=["基础"],
)
async def health_check():
    payload = HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        timestamp=datetime.now().isoformat(),
    )
    return success(payload, message=Messages.QUERY_SUCCESS)


@router.get(
    "/app-info",
    response_model=BaseResponse[AppInfoResponse],
    summary="获取应用信息",
    tags=["基础"],
)
async def get_app_info():
    """获取应用基本信息（名称、版本、标题等）"""
    payload = AppInfoResponse(
        name=settings.APP_NAME,
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        copyright_year=settings.COPYRIGHT_YEAR,
    )
    return success(payload, message=Messages.QUERY_SUCCESS)


@router.get(
    "/health/detailed",
    summary="详细健康检查",
    tags=["基础"],
    response_model=BaseResponse[dict[str, Any]],
)
async def detailed_health_check(
    include_details: bool = Query(default=True, description="是否包含详细信息"),
) -> JSONResponse:
    """
    详细健康检查端点

    返回所有服务组件的健康状态，包括：
    - 数据库连接
    - Redis 连接（如启用）
    - 熔断器状态
    - 系统资源使用
    """
    health = await health_checker.check_all()

    # 根据状态返回不同的 HTTP 状态码
    if health.status == HealthStatus.HEALTHY:
        status_code = 200
    elif health.status == HealthStatus.DEGRADED:
        status_code = 200  # 降级但仍可用
    else:
        status_code = 503  # 服务不可用

    response_data = health.to_dict()
    response_data["version"] = settings.APP_VERSION

    if not include_details:
        # 简化响应
        response_data = {
            "status": health.status.value,
            "version": settings.APP_VERSION,
            "timestamp": health.timestamp,
            "summary": health.summary,
        }

    response = success(response_data, message=Messages.QUERY_SUCCESS, code=status_code)
    return JSONResponse(content=response.model_dump(mode="json"), status_code=status_code)


@router.get(
    "/health/live",
    response_model=BaseResponse[ProbeStatusResponse],
    summary="存活检查 (Kubernetes liveness)",
    tags=["基础"],
)
async def liveness_check() -> JSONResponse:
    """
    Kubernetes 存活探针端点

    只检查应用是否存活，不检查依赖服务；套 5s 超时防止 event loop 被阻塞时探针卡住
    """
    try:
        is_alive = await asyncio.wait_for(health_checker.liveness(), timeout=_PROBE_TIMEOUT)
    except TimeoutError:
        is_alive = False

    status_code = status.HTTP_200_OK if is_alive else status.HTTP_503_SERVICE_UNAVAILABLE
    probe_status = "alive" if is_alive else "dead"
    payload = ProbeStatusResponse(status=probe_status, timestamp=datetime.now().isoformat())
    response = success(payload, message=Messages.QUERY_SUCCESS, code=status_code)
    return JSONResponse(content=response.model_dump(mode="json"), status_code=status_code)


@router.get(
    "/health/ready",
    response_model=BaseResponse[ProbeStatusResponse],
    summary="就绪检查 (Kubernetes readiness)",
    tags=["基础"],
)
async def readiness_check() -> JSONResponse:
    """
    Kubernetes 就绪探针端点

    检查应用是否准备好接收流量；套 5s 超时防止依赖（DB/Redis）响应慢时探针卡住
    """
    try:
        is_ready = await asyncio.wait_for(health_checker.readiness(), timeout=_PROBE_TIMEOUT)
    except TimeoutError:
        is_ready = False

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    probe_status = "ready" if is_ready else "not_ready"
    payload = ProbeStatusResponse(status=probe_status, timestamp=datetime.now().isoformat())
    response = success(payload, message=Messages.QUERY_SUCCESS, code=status_code)
    return JSONResponse(content=response.model_dump(mode="json"), status_code=status_code)


@router.post(
    "/auth/login",
    response_model=BaseResponse[UserLoginResponse],
    summary="用户登录",
    tags=["认证"],
)
async def login(request: UserLoginRequest, http_request: Request):
    # P1-06: 登录限流 / 审计使用受信代理白名单解析后的客户端 IP
    #   生产 Nginx 会把真实 IP 放到 X-Real-IP / X-Forwarded-For,如果这里直接
    #   拿 socket 对端 IP,所有用户都会共用同一个反代 IP 的限流桶(默认 5/min),
    #   一个用户失败几次就把整个入口打爆。get_client_ip 只在 socket 对端命中
    #   ANTCODE_TRUSTED_PROXIES 白名单时才采信转发头,防止外部伪造。
    ip_address = get_client_ip(http_request) if http_request.client else None
    # get_client_ip 兜底会返回 "unknown"——限流键要求 falsy 才跳过,归一为 None
    if ip_address == "unknown":
        ip_address = None
    user_agent = http_request.headers.get("user-agent")

    # T7-B4a (P1-2): 登录专项限流 + 账户锁定
    from antcode_core.common.security.login_guard import (
        check_ip_rate,
        check_user_rate,
        clear_failures,
        is_account_locked,
        record_failure,
    )

    if ip_address and not await check_ip_rate(ip_address):
        await audit_service.log_login(
            username=request.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message="登录 IP 限流触发",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后再试",
        )
    if request.username and not await check_user_rate(request.username):
        await audit_service.log_login(
            username=request.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message="登录用户名限流触发",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="该账户登录尝试过于频繁，请稍后再试",
        )
    locked, remain = await is_account_locked(request.username)
    if locked:
        await audit_service.log_login(
            username=request.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message=f"账户锁定中 ({remain}s)",
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"账户已锁定，请 {remain} 秒后再试",
        )

    password = request.password
    if request.encrypted_password:
        if not settings.LOGIN_PASSWORD_ENCRYPTION_ENABLED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="登录加密未启用")
        try:
            password = login_password_crypto.decrypt_password(
                request.encrypted_password,
                algorithm=request.encryption,
                key_id=request.key_id,
            )
        except LoginPasswordCryptoError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    elif settings.LOGIN_PASSWORD_ENCRYPTION_REQUIRED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="密码必须加密传输")

    user = await user_service.authenticate_user(request.username, password)

    if not user:
        # T7-B4a: 记登录失败 + 累加计数（可能触发新锁定）
        failures, newly_locked = await record_failure(request.username)
        err_msg = "用户名或密码错误"
        if newly_locked:
            err_msg += f"（连续失败 {failures} 次，账户已被锁定 "
            err_msg += f"{settings.LOGIN_LOCKOUT_DURATION_SEC // 60} 分钟）"
        await audit_service.log_login(
            username=request.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message=err_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err_msg,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        # 记录登录失败
        await audit_service.log_login(
            username=request.username,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message="账户已禁用",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户已禁用")

    # 记录登录成功 + T7-B4a: 清失败计数
    await clear_failures(user.username)
    await audit_service.log_login(
        username=user.username,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        success=True,
    )

    await user_service.clear_cache()

    access_token = jwt_auth.create_access_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        role=user.role.value,
    )
    refresh_token = jwt_auth.create_refresh_token(user_id=user.id, username=user.username)

    user_payload = UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email or "",
        is_active=user.is_active,
        is_admin=user.is_admin,
        role=user.role.value,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
    )
    payload = UserLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        user=user_payload,
    )
    return success(payload, message=Messages.LOGIN_SUCCESS)


@router.get(
    "/auth/public-key",
    response_model=BaseResponse[LoginPublicKeyResponse],
    summary="获取登录公钥",
    tags=["认证"],
)
async def get_login_public_key():
    if not settings.LOGIN_PASSWORD_ENCRYPTION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="登录加密未启用")
    payload = LoginPublicKeyResponse(**login_password_crypto.public_key_payload())
    return success(payload, message=Messages.QUERY_SUCCESS)


@router.post(
    "/auth/refresh",
    response_model=BaseResponse[UserLoginResponse],
    summary="刷新令牌",
    tags=["认证"],
)
async def refresh_token(request: RefreshTokenRequest):
    """使用刷新令牌获取新的访问令牌"""
    try:
        token_data = verify_refresh_token(request.refresh_token)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await user_service.get_user_by_id(token_data.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户不可用")

    access_token = jwt_auth.create_access_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        role=user.role.value,
    )
    refresh_token_value = jwt_auth.create_refresh_token(user_id=user.id, username=user.username)

    user_payload = UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email or "",
        is_active=user.is_active,
        is_admin=user.is_admin,
        role=user.role.value,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
    )
    payload = UserLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token_value,
        token_type="bearer",
        expires_in=int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        user=user_payload,
    )
    return success(payload, message=Messages.OPERATION_SUCCESS)


@router.get(
    "/auth/check-username/{username}",
    response_model=BaseResponse[dict],
    summary="检查用户名可用性",
    tags=["认证"],
)
async def check_username(
    username: str,
):
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=AUTH_SELF_SERVICE_DISABLED_DETAIL,
    )


@router.get(
    "/auth/check-email/{email}",
    response_model=BaseResponse[dict],
    summary="检查邮箱可用性",
    tags=["认证"],
)
async def check_email(
    email: str,
):
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=AUTH_SELF_SERVICE_DISABLED_DETAIL,
    )


@router.get(
    "/auth/permissions",
    response_model=BaseResponse[dict[str, list[str]]],
    summary="获取用户权限",
    tags=["认证"],
)
async def get_permissions(current_user: TokenData = Depends(get_current_user)):
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    permissions = ["admin"] if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN) else ["user"]
    return success({"permissions": permissions}, message=Messages.QUERY_SUCCESS)
