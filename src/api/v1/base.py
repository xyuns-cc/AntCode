"""基础接口"""
from fastapi import APIRouter, HTTPException, status, Request

from src.core.security.auth import jwt_auth
from src.core.config import settings
from src.schemas import HealthResponse, UserLoginRequest, UserLoginResponse
from src.schemas.common import BaseResponse
from src.core.response import success, Messages
from src.services.users.user_service import user_service
from src.services.audit import audit_service

router = APIRouter()


@router.get(
    "/health",
    response_model=BaseResponse[HealthResponse],
    summary="健康检查",
    tags=["基础"]
)
async def health_check():
    from datetime import datetime
    payload = HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        timestamp=datetime.now().isoformat()
    )
    return success(payload, message=Messages.QUERY_SUCCESS)


@router.post(
    "/auth/login",
    response_model=BaseResponse[UserLoginResponse],
    summary="用户登录",
    tags=["认证"]
)
async def login(request: UserLoginRequest, http_request: Request):
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    user = await user_service.authenticate_user(request.username, request.password)

    if not user:
        # 记录登录失败
        await audit_service.log_login(
            username=request.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message="用户名或密码错误"
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
            error_message="账户已禁用"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已禁用"
        )

    # 记录登录成功
    await audit_service.log_login(
        username=user.username,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        success=True
    )

    access_token = jwt_auth.create_access_token(user_id=user.id, username=user.username)

    payload = UserLoginResponse(
        access_token=access_token,
        user_id=user.public_id,
        username=user.username,
        is_admin=user.is_admin
    )
    return success(payload, message=Messages.LOGIN_SUCCESS)
