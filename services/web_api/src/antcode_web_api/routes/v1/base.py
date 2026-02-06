"""基础接口"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from antcode_core.common.config import settings
from antcode_web_api.response import Messages, success
from antcode_core.common.security.auth import get_current_user, jwt_auth, verify_refresh_token
from antcode_core.common.security.login_crypto import (
    LoginPasswordCryptoError,
    login_password_crypto,
)
from antcode_core.domain.models import User
from antcode_core.domain.schemas import (
    AppInfoResponse,
    HealthResponse,
    LoginPublicKeyResponse,
    UserLoginRequest,
    UserLoginResponse,
    UserResponse,
)
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.users.user_service import user_service
from antcode_core.infrastructure.resilience.health import HealthStatus, health_checker

router = APIRouter()


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=100)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


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
    response_model=None,
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

    return JSONResponse(content=response_data, status_code=status_code)


@router.get(
    "/health/live",
    summary="存活检查 (Kubernetes liveness)",
    tags=["基础"],
)
async def liveness_check() -> dict[str, Any]:
    """
    Kubernetes 存活探针端点

    只检查应用是否存活，不检查依赖服务
    """
    is_alive = await health_checker.liveness()

    if is_alive:
        return {"status": "alive", "timestamp": datetime.now().isoformat()}

    return JSONResponse(
        content={"status": "dead", "timestamp": datetime.now().isoformat()},
        status_code=503,
    )


@router.get(
    "/health/ready",
    summary="就绪检查 (Kubernetes readiness)",
    tags=["基础"],
)
async def readiness_check() -> dict[str, Any]:
    """
    Kubernetes 就绪探针端点

    检查应用是否准备好接收流量
    """
    is_ready = await health_checker.readiness()

    if is_ready:
        return {"status": "ready", "timestamp": datetime.now().isoformat()}

    return JSONResponse(
        content={"status": "not_ready", "timestamp": datetime.now().isoformat()},
        status_code=503,
    )


@router.post(
    "/auth/login",
    response_model=BaseResponse[UserLoginResponse],
    summary="用户登录",
    tags=["认证"],
)
async def login(request: UserLoginRequest, http_request: Request):
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

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
        # 记录登录失败
        await audit_service.log_login(
            username=request.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message="用户名或密码错误",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
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

    # 记录登录成功
    await audit_service.log_login(
        username=user.username,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        success=True,
    )

    permissions = ["admin"] if user.is_admin else []
    access_token = jwt_auth.create_access_token(
        user_id=user.id, username=user.username, permissions=permissions
    )
    refresh_token = jwt_auth.create_refresh_token(user_id=user.id, username=user.username)

    user_payload = UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email or "",
        is_active=user.is_active,
        is_admin=user.is_admin,
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

    permissions = ["admin"] if user.is_admin else []
    access_token = jwt_auth.create_access_token(
        user_id=user.id, username=user.username, permissions=permissions
    )
    refresh_token_value = jwt_auth.create_refresh_token(user_id=user.id, username=user.username)

    user_payload = UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email or "",
        is_active=user.is_active,
        is_admin=user.is_admin,
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


@router.post(
    "/auth/verify-email",
    response_model=BaseResponse[dict],
    summary="验证邮箱",
    tags=["认证"],
)
async def verify_email(request: VerifyEmailRequest):
    """验证邮箱（当前实现为占位验证）"""
    try:
        _ = jwt_auth.verify_token(request.token, expected_type="verify")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return success({"verified": True}, message="邮箱验证成功")


@router.post(
    "/auth/forgot-password",
    response_model=BaseResponse[dict],
    summary="发送重置密码邮件",
    tags=["认证"],
)
async def forgot_password(request: ForgotPasswordRequest):
    """发送重置密码邮件（当前返回重置令牌，实际邮件发送待接入）"""
    user = await User.get_or_none(email=request.email)
    token = None
    if user:
        token = jwt_auth.create_action_token(
            user_id=user.id,
            username=user.username,
            token_type="reset",
            expires_delta=timedelta(minutes=30),
        )
    return success(
        {"token": token},
        message="重置邮件已发送（如未收到，请联系管理员）",
    )


@router.post(
    "/auth/reset-password",
    response_model=BaseResponse[dict],
    summary="重置密码",
    tags=["认证"],
)
async def reset_password(request: ResetPasswordRequest):
    """使用重置令牌修改密码"""
    try:
        token_data = jwt_auth.verify_token(request.token, expected_type="reset")
        await user_service.reset_user_password(token_data.user_id, request.new_password)
        return success({"reset": True}, message="密码已重置")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/auth/check-username/{username}",
    response_model=BaseResponse[dict],
    summary="检查用户名可用性",
    tags=["认证"],
)
async def check_username(username: str):
    user = await user_service.get_user_by_username(username)
    return success({"available": user is None}, message=Messages.QUERY_SUCCESS)


@router.get(
    "/auth/check-email/{email}",
    response_model=BaseResponse[dict],
    summary="检查邮箱可用性",
    tags=["认证"],
)
async def check_email(email: str):
    user = await User.get_or_none(email=email)
    return success({"available": user is None}, message=Messages.QUERY_SUCCESS)


@router.get(
    "/auth/permissions",
    response_model=BaseResponse[dict],
    summary="获取用户权限",
    tags=["认证"],
)
async def get_permissions(current_user=Depends(get_current_user)):
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    permissions = ["admin"] if user.is_admin else ["user"]
    return success({"permissions": permissions}, message=Messages.QUERY_SUCCESS)
