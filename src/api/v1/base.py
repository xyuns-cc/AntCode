"""基础接口"""
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status, Request, Query
from fastapi.responses import JSONResponse

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
    payload = HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        timestamp=datetime.now().isoformat()
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
    - gRPC 服务状态
    - 熔断器状态
    - 系统资源使用
    """
    from src.core.resilience.health import health_checker, HealthStatus
    
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
async def liveness_check() -> Dict[str, Any]:
    """
    Kubernetes 存活探针端点
    
    只检查应用是否存活，不检查依赖服务
    """
    from src.core.resilience.health import health_checker
    
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
async def readiness_check() -> Dict[str, Any]:
    """
    Kubernetes 就绪探针端点
    
    检查应用是否准备好接收流量
    """
    from src.core.resilience.health import health_checker
    
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
