"""基础接口"""
from fastapi import APIRouter, HTTPException, status

from src.core.auth import jwt_auth
from src.core.config import settings
from src.schemas import HealthResponse, UserLoginRequest, UserLoginResponse
from src.schemas.common import BaseResponse
from src.core.response import success, error, ResponseCode, Messages
from src.services.users.user_service import user_service

router = APIRouter()


@router.get("/health", response_model=BaseResponse[HealthResponse], summary="健康检查", tags=["基础"])
async def health_check():
    from datetime import datetime
    payload = HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        timestamp=datetime.now().isoformat()
    )
    return success(payload, message=Messages.QUERY_SUCCESS)


@router.post("/auth/login", response_model=BaseResponse[UserLoginResponse], summary="用户登录", tags=["认证"])
async def login(request: UserLoginRequest):
    user = await user_service.authenticate_user(request.username, request.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已禁用"
        )

    access_token = jwt_auth.create_access_token(user_id=user.id, username=user.username)

    payload = UserLoginResponse(
        access_token=access_token,
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin
    )
    return success(payload, message=Messages.LOGIN_SUCCESS)
