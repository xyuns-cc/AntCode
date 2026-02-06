"""
依赖注入模块

提供 FastAPI 路由的依赖注入函数
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from antcode_core.common.security.auth import jwt_auth
from antcode_core.domain.models import User

# HTTP Bearer 认证方案
security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> User:
    """获取当前认证用户

    Args:
        credentials: HTTP Bearer 凭证

    Returns:
        User: 当前用户对象

    Raises:
        HTTPException: 认证失败时抛出 401
    """
    try:
        token = credentials.credentials
        token_data = jwt_auth.verify_token(token)

        # 从数据库获取用户
        from antcode_core.application.services.users.user_service import user_service
        user = await user_service.get_user_by_id(token_data.user_id)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在"
            )

        return user

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"认证失败: {str(e)}"
        )


async def get_current_admin_user(
    current_user: Annotated[User, Depends(get_current_user)]
) -> User:
    """获取当前管理员用户

    Args:
        current_user: 当前用户

    Returns:
        User: 管理员用户对象

    Raises:
        HTTPException: 非管理员时抛出 403
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


# 类型别名，方便使用
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdminUser = Annotated[User, Depends(get_current_admin_user)]
