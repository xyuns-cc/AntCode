"""权限管理模块

提供权限定义和检查功能。
"""

from enum import Enum

from antcode_core.common.exceptions import AuthorizationError


class Permission(str, Enum):
    """权限枚举"""

    # 用户权限
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    USER_DELETE = "user:delete"
    USER_ADMIN = "user:admin"

    # 项目权限
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    PROJECT_ADMIN = "project:admin"

    # 任务权限
    TASK_READ = "task:read"
    TASK_WRITE = "task:write"
    TASK_DELETE = "task:delete"
    TASK_EXECUTE = "task:execute"
    TASK_ADMIN = "task:admin"

    # Worker 权限
    WORKER_READ = "worker:read"
    WORKER_WRITE = "worker:write"
    WORKER_DELETE = "worker:delete"
    WORKER_ADMIN = "worker:admin"

    # 系统权限
    SYSTEM_READ = "system:read"
    SYSTEM_WRITE = "system:write"
    SYSTEM_ADMIN = "system:admin"

    # 超级管理员
    SUPER_ADMIN = "super:admin"


# 角色权限映射
ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "user": {
        Permission.USER_READ,
        Permission.PROJECT_READ,
        Permission.TASK_READ,
        Permission.WORKER_READ,
    },
    "operator": {
        Permission.USER_READ,
        Permission.PROJECT_READ,
        Permission.PROJECT_WRITE,
        Permission.TASK_READ,
        Permission.TASK_WRITE,
        Permission.TASK_EXECUTE,
        Permission.WORKER_READ,
    },
    "admin": {
        Permission.USER_READ,
        Permission.USER_WRITE,
        Permission.PROJECT_READ,
        Permission.PROJECT_WRITE,
        Permission.PROJECT_DELETE,
        Permission.PROJECT_ADMIN,
        Permission.TASK_READ,
        Permission.TASK_WRITE,
        Permission.TASK_DELETE,
        Permission.TASK_EXECUTE,
        Permission.TASK_ADMIN,
        Permission.WORKER_READ,
        Permission.WORKER_WRITE,
        Permission.WORKER_DELETE,
        Permission.WORKER_ADMIN,
        Permission.SYSTEM_READ,
        Permission.SYSTEM_WRITE,
    },
    "super_admin": {
        Permission.SUPER_ADMIN,
        # 超级管理员拥有所有权限
        *Permission,
    },
}


def get_role_permissions(role: str) -> set[Permission]:
    """获取角色的权限集合

    Args:
        role: 角色名称

    Returns:
        权限集合
    """
    return ROLE_PERMISSIONS.get(role, set())


def check_permission(
    user_permissions: set[Permission] | list[Permission],
    required_permission: Permission,
    raise_error: bool = True,
) -> bool:
    """检查用户是否拥有指定权限

    Args:
        user_permissions: 用户拥有的权限集合
        required_permission: 需要的权限
        raise_error: 是否在权限不足时抛出异常

    Returns:
        是否拥有权限

    Raises:
        AuthorizationError: 权限不足时抛出（如果 raise_error=True）
    """
    if isinstance(user_permissions, list):
        user_permissions = set(user_permissions)

    # 超级管理员拥有所有权限
    if Permission.SUPER_ADMIN in user_permissions:
        return True

    has_permission = required_permission in user_permissions

    if not has_permission and raise_error:
        raise AuthorizationError(f"权限不足: 需要 {required_permission.value}")

    return has_permission


def check_any_permission(
    user_permissions: set[Permission] | list[Permission],
    required_permissions: list[Permission],
    raise_error: bool = True,
) -> bool:
    """检查用户是否拥有任一指定权限

    Args:
        user_permissions: 用户拥有的权限集合
        required_permissions: 需要的权限列表（满足任一即可）
        raise_error: 是否在权限不足时抛出异常

    Returns:
        是否拥有权限
    """
    if isinstance(user_permissions, list):
        user_permissions = set(user_permissions)

    # 超级管理员拥有所有权限
    if Permission.SUPER_ADMIN in user_permissions:
        return True

    has_permission = any(p in user_permissions for p in required_permissions)

    if not has_permission and raise_error:
        perm_names = [p.value for p in required_permissions]
        raise AuthorizationError(f"权限不足: 需要以下权限之一 {perm_names}")

    return has_permission


def check_all_permissions(
    user_permissions: set[Permission] | list[Permission],
    required_permissions: list[Permission],
    raise_error: bool = True,
) -> bool:
    """检查用户是否拥有所有指定权限

    Args:
        user_permissions: 用户拥有的权限集合
        required_permissions: 需要的权限列表（必须全部满足）
        raise_error: 是否在权限不足时抛出异常

    Returns:
        是否拥有权限
    """
    if isinstance(user_permissions, list):
        user_permissions = set(user_permissions)

    # 超级管理员拥有所有权限
    if Permission.SUPER_ADMIN in user_permissions:
        return True

    missing = [p for p in required_permissions if p not in user_permissions]

    if missing and raise_error:
        perm_names = [p.value for p in missing]
        raise AuthorizationError(f"权限不足: 缺少权限 {perm_names}")

    return len(missing) == 0
