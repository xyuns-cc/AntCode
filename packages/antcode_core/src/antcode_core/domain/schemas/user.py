"""
用户 Schema

用户相关的请求和响应模式。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserLoginRequest(BaseModel):
    """用户登录请求"""

    username: str = Field(..., min_length=1, max_length=50)
    password: str | None = Field(default=None, min_length=1)
    encrypted_password: str | None = Field(default=None, min_length=1)
    encryption: str | None = Field(default=None, max_length=50)
    key_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_password(self) -> "UserLoginRequest":
        if not self.password and not self.encrypted_password:
            raise ValueError("password 或 encrypted_password 至少需要一个")
        return self


_ADMIN_ROLES = {"admin", "super_admin"}


def _validate_role_value(role: str | None) -> None:
    if role is None:
        return
    if role not in {"user", "admin", "super_admin"}:
        raise ValueError("role 取值必须是 user / admin / super_admin")


def _validate_role_admin_consistency(is_admin: bool | None, role: str | None) -> None:
    """is_admin 与 role 必须一致，防客户端注入不一致组合。"""
    if is_admin is None or role is None:
        return
    role_says_admin = role in _ADMIN_ROLES
    if is_admin != role_says_admin:
        raise ValueError("is_admin 与 role 不一致：is_admin 必须 = (role in {admin, super_admin})")


class UserCreateRequest(BaseModel):
    """用户创建请求"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    email: str | None = Field(None, max_length=100)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    role: str | None = Field(None, max_length=20)

    @model_validator(mode="after")
    def validate_role_consistency(self) -> "UserCreateRequest":
        _validate_role_value(self.role)
        _validate_role_admin_consistency(self.is_admin, self.role)
        return self


class UserUpdateRequest(BaseModel):
    """通用用户更新请求。

    安全约束：这是所有登录用户都能触达的通用更新接口，因此**严禁**在此暴露
    权限相关或凭证相关字段。以下字段必须走各自的独立端点：

    - ``role`` / ``is_admin``：必须走 super-admin-only 的 ``PATCH /users/{id}/role``
      （载荷：:class:`AdminUserRoleUpdateRequest`）。
    - ``password`` / ``new_password``：改自己密码走 ``POST /users/change-password``；
      super-admin 给他人重置走 ``PUT /users/{id}/reset-password``。

    使用 ``extra="forbid"`` 让 Pydantic 直接把带这些字段的请求以 422 拒掉，
    实现深度防御（即便调用方漏检也不会被 setattr 到模型上）。
    """

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(None, min_length=3, max_length=50)
    email: str | None = Field(None, max_length=100)
    is_active: bool | None = None


class AdminUserRoleUpdateRequest(BaseModel):
    """专用于「改用户角色」的请求，仅 SUPER_ADMIN 可调。

    必须同时显式传入 ``old_role`` 和 ``new_role``：
    - ``old_role`` 用于乐观并发防护，避免 stale UI 触发意外提权/降权；
    - 路由层会校验 ``old_role`` 与 DB 中的当前 role 一致。
    """

    model_config = ConfigDict(extra="forbid")

    old_role: str = Field(..., max_length=20)
    new_role: str = Field(..., max_length=20)

    @model_validator(mode="after")
    def validate_role_values(self) -> "AdminUserRoleUpdateRequest":
        _validate_role_value(self.old_role)
        _validate_role_value(self.new_role)
        return self


# 兼容旧命名，避免下游引用突然断裂；新代码请直接用 AdminUserRoleUpdateRequest
UserRoleUpdateRequest = AdminUserRoleUpdateRequest


class UserPasswordUpdateRequest(BaseModel):
    """用户密码更新请求"""

    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class UserAdminPasswordUpdateRequest(BaseModel):
    """管理员重置密码请求"""

    new_password: str = Field(..., min_length=8)


class UserResponse(BaseModel):
    """用户响应"""

    id: str = Field(..., description="用户公开ID")
    username: str
    email: str = ""
    is_active: bool
    is_admin: bool
    role: str = "user"
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
    is_online: bool = False

    model_config = ConfigDict(from_attributes=True)


class UserSimpleResponse(BaseModel):
    """用户简要响应"""

    id: str = Field(..., description="用户公开ID")
    username: str

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    """用户列表响应"""

    items: list[UserResponse]
    total: int
    page: int
    size: int


class UserLoginResponse(BaseModel):
    """用户登录响应"""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None
    user: UserResponse


class LoginPublicKeyResponse(BaseModel):
    """登录公钥响应"""

    algorithm: str
    key_id: str
    public_key: str


__all__ = [
    "UserLoginRequest",
    "UserCreateRequest",
    "UserUpdateRequest",
    "AdminUserRoleUpdateRequest",
    "UserRoleUpdateRequest",
    "UserPasswordUpdateRequest",
    "UserAdminPasswordUpdateRequest",
    "UserResponse",
    "UserSimpleResponse",
    "UserListResponse",
    "UserLoginResponse",
    "LoginPublicKeyResponse",
]
