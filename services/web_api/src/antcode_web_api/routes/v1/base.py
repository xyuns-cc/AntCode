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
    get_optional_current_user,
    jwt_auth,
    record_refresh_session,
    revoke_refresh_session,
    rotate_refresh_session,
    verify_refresh_token,
)
from antcode_core.common.security.login_crypto import (
    LoginPasswordCryptoError,
    login_password_crypto,
)
from antcode_core.common.security.permissions import get_role_permissions
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
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from antcode_web_api.middleware.middleware import get_client_ip
from antcode_web_api.response import Messages, success

# K8s probe 单次超时（秒）：任何依赖探测卡住不能拖垮探针
_PROBE_TIMEOUT = 5.0
_REFRESH_COOKIE_NAME = "antcode_refresh"
_REFRESH_COOKIE_PATH = "/api/v1/auth"

router = APIRouter()

# 企业内网部署：关闭自助找回/重置密码与公开可用性枚举接口
AUTH_SELF_SERVICE_DISABLED_DETAIL = "企业内部系统已禁用自助认证接口，请联系系统管理员"


class RefreshTokenRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=1)


class ProbeStatusResponse(BaseModel):
    status: str
    timestamp: str


def _session_device_info(request: Request, client_ip: str | None = None) -> str:
    user_agent = request.headers.get("user-agent", "unknown")
    resolved_ip = client_ip or get_client_ip(request)
    return f"{user_agent}|{resolved_ip}"[:256]


def _refresh_cookie_secure() -> bool:
    configured = settings.AUTH_COOKIE_SECURE
    if configured is not None:
        return configured
    return settings.SERVER_DOMAIN.strip().lower() not in {"", "localhost", "127.0.0.1"}


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        max_age=int(settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60),
        path=_REFRESH_COOKIE_PATH,
        secure=_refresh_cookie_secure(),
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path=_REFRESH_COOKIE_PATH,
        secure=_refresh_cookie_secure(),
        httponly=True,
        samesite="strict",
    )


def _resolve_refresh_token(request: RefreshTokenRequest | None, http_request: Request) -> str:
    token = http_request.cookies.get(_REFRESH_COOKIE_NAME)
    if not token and request is not None:
        token = request.refresh_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少刷新令牌")
    return token


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
    include_details: bool = Query(default=False, description="是否包含详细信息（仅管理员）"),
    current_user: TokenData | None = Depends(get_optional_current_user),
) -> JSONResponse:
    """
    详细健康检查端点

    返回所有服务组件的健康状态，包括：
    - 数据库连接
    - Redis 连接（如启用）
    - 熔断器状态
    - 系统资源使用

    P2-03：匿名调用只返回 status/version/timestamp/summary；
    ``include_details=true`` 需管理员，否则同样降级为 summary（不再直接
    对匿名用户暴露 Worker host/port、熔断器状态、内存磁盘和异常文本）。
    """
    is_admin = bool(current_user and getattr(current_user, "is_admin", False))
    if not is_admin or not include_details:
        response_data = {
            "status": HealthStatus.HEALTHY.value,
            "version": settings.APP_VERSION,
            "timestamp": datetime.now().isoformat(),
            "summary": {"healthy": 1, "degraded": 0, "unhealthy": 0},
        }
        response = success(response_data, message=Messages.QUERY_SUCCESS)
        return JSONResponse(content=response.model_dump(mode="json"), status_code=status.HTTP_200_OK)

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
    if is_ready:
        from antcode_web_api.streams.ingest_follower import ingest_log_follower

        is_ready = ingest_log_follower.healthy()

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
async def login(request: UserLoginRequest, http_request: Request, response: Response):
    # P1-06: 登录限流 / 审计使用受信代理白名单解析后的客户端 IP
    #   生产 Nginx 会把真实 IP 放到 X-Real-IP / X-Forwarded-For,如果这里直接
    #   拿 socket 对端 IP,所有用户都会共用同一个反代 IP 的限流桶(默认 5/min),
    #   一个用户失败几次就把整个入口打爆。get_client_ip 只在 socket 对端命中
    #   ANTCODE_TRUSTED_PROXIES 白名单时才采信转发头,防止外部伪造。
    ip_address = get_client_ip(http_request) if http_request.client else None
    if ip_address == "unknown":
        ip_address = None
    client_scope = ip_address or "unknown"
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
    if request.username and not await check_user_rate(request.username, client_scope):
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
    locked, remain = await is_account_locked(request.username, client_scope)
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
        failures, newly_locked = await record_failure(request.username, client_scope)
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

    await clear_failures(user.username, client_scope)
    await audit_service.log_login(
        username=user.username,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        success=True,
    )

    await user_service.clear_cache()

    refresh_token, session_jti, expires_at = jwt_auth.create_refresh_token(
        user_id=user.id,
        username=user.username,
    )
    await record_refresh_session(
        user_id=user.id,
        jti=session_jti,
        expires_at=expires_at,
        device_info=_session_device_info(http_request, ip_address),
    )
    access_token = jwt_auth.create_access_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        role=user.role.value,
        session_jti=session_jti,
    )

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
        refresh_token=None,
        token_type="bearer",
        expires_in=int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        user=user_payload,
    )
    _set_refresh_cookie(response, refresh_token)
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
async def refresh_token(
    http_request: Request,
    response: Response,
    request: RefreshTokenRequest | None = None,
):
    """使用刷新令牌获取新的访问令牌"""
    refresh_token_input = _resolve_refresh_token(request, http_request)
    try:
        token_data = await verify_refresh_token(refresh_token_input)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("刷新令牌校验失败")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token 无效") from exc

    user = await user_service.get_user_by_id(token_data.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户不可用")

    refresh_token_value, new_jti, expires_at = jwt_auth.create_refresh_token(
        user_id=user.id,
        username=user.username,
    )
    await rotate_refresh_session(
        user_id=user.id,
        previous_jti=token_data.session_jti or "",
        new_jti=new_jti,
        expires_at=expires_at,
        device_info=_session_device_info(http_request),
    )
    access_token = jwt_auth.create_access_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        role=user.role.value,
        session_jti=new_jti,
    )

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
        refresh_token=None,
        token_type="bearer",
        expires_in=int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        user=user_payload,
    )
    _set_refresh_cookie(response, refresh_token_value)
    return success(payload, message=Messages.OPERATION_SUCCESS)


@router.post(
    "/auth/logout",
    response_model=BaseResponse[None],
    summary="用户登出",
    tags=["认证"],
)
async def logout(response: Response, current_user: TokenData = Depends(get_current_user)):
    """撤销当前 access token 绑定的服务端会话。"""
    await revoke_refresh_session(
        user_id=current_user.user_id,
        jti=current_user.session_jti or "",
    )
    _clear_refresh_cookie(response)
    return success(None, message="已退出登录")


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
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    permissions = sorted(permission.value for permission in get_role_permissions(role))
    return success({"permissions": permissions}, message=Messages.QUERY_SUCCESS)


@router.get(
    "/auth/me",
    response_model=BaseResponse[UserResponse],
    summary="获取当前用户信息",
    tags=["认证"],
)
async def get_me(current_user: TokenData = Depends(get_current_user)):
    """
    P2-15：轻量 /auth/me，让前端能按需刷新 localStorage 里的用户信息
    （用户名/角色/is_active），避免菜单/角色展示与服务端权威状态长期不一致。
    只返回展示字段，不涉及权限颁发，仅要求登录用户。
    """
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return success(UserResponse.model_validate(user), message=Messages.QUERY_SUCCESS)
