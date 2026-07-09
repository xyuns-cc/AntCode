"""
依赖注入模块

提供 FastAPI 路由的依赖注入函数
"""

from collections.abc import Callable
from typing import Annotated

from antcode_core.common.security.auth import jwt_auth
from antcode_core.common.security.permissions import (
    Permission,
    get_role_permissions,
)
from antcode_core.domain.models import User, UserRole
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# HTTP Bearer 认证方案
security = HTTPBearer()


async def get_current_user(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> User:
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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

        return user

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"认证失败: {str(e)}")


async def get_current_admin_user(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """获取当前管理员用户

    Args:
        current_user: 当前用户

    Returns:
        User: 管理员用户对象

    Raises:
        HTTPException: 非管理员时抛出 403
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


# 类型别名，方便使用
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdminUser = Annotated[User, Depends(get_current_admin_user)]


def require_role(*roles: UserRole) -> Callable:
    """生成一个 FastAPI Depends：只允许具备指定角色的用户。

    用法：
        @router.get("/admin/things", dependencies=[Depends(require_role(UserRole.ADMIN))])
        @router.get("/secret", )
        async def x(user: User = Depends(require_role(UserRole.SUPER_ADMIN))):
            ...
    """
    allowed = set(roles)

    async def _dep(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足",
            )
        return current_user

    return _dep


def require_permission(*perms: Permission) -> Callable:
    """生成一个 FastAPI Depends：只允许具备所有指定权限的用户。"""
    required = set(perms)

    async def _dep(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        user_perms = get_role_permissions(
            current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
        )
        if Permission.SUPER_ADMIN in user_perms:
            return current_user
        missing = required - user_perms
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足: 缺少 {', '.join(p.value for p in missing)}",
            )
        return current_user

    return _dep
