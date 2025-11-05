"""
基础API接口
包含认证、健康检查等基础功能
"""
from fastapi import APIRouter, HTTPException, status

from src.core.auth import jwt_auth
from src.core.config import settings
from src.schemas import HealthResponse, UserLoginRequest, UserLoginResponse
from src.schemas.common import BaseResponse
from src.core.response import success, error
from src.core.response_codes import ResponseCode
from src.core.messages import Messages
from src.services.users.user_service import user_service

router = APIRouter()


@router.get(
    "/health",
    response_model=BaseResponse[HealthResponse],
    summary="健康检查",
    description="检查服务运行状态和基本信息",
    tags=["基础功能"]
)
async def health_check():
    """健康检查接口"""
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
    description="用户登录验证，返回JWT访问令牌",
    response_description="返回JWT令牌和用户基本信息",
    tags=["认证"]
)
async def login(request: UserLoginRequest):
    """用户登录"""
    # 验证用户凭据
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
            detail="用户账号已被禁用"
        )

    # 创建访问令牌
    access_token = jwt_auth.create_access_token(
        user_id=user.id,
        username=user.username
    )

    payload = UserLoginResponse(
        access_token=access_token,
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin
    )
    return success(payload, message=Messages.LOGIN_SUCCESS)
