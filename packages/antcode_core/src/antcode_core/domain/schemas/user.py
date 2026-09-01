"""
用户 Schema

用户相关的请求和响应模式。
"""

from datetime import datetime
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


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
    """is_admin 与 role 必须一致，防客户端注入不一致组合。

    ``is_admin=True`` 而 ``role`` 缺省是**欠定**，不是"默认普通用户"：``role`` 是
    权限的唯一权威（``User._sync_admin_flag`` 按它反推 ``is_admin``），而管理员有
    admin / super_admin 两种，一个布尔位推不出是哪一种。这里以前直接放行，请求会
    带着 ``role=user`` 落库、``is_admin`` 被翻回 False，却仍回 201「创建成功」——
    权限字段上的静默降级。改为显式拒绝，由调用方指明 role。
    """
    if is_admin and role is None:
        raise ValueError("is_admin=true 时必须显式提供 role：admin 或 super_admin")
    if is_admin is None or role is None:
        return
    role_says_admin = role in _ADMIN_ROLES
    if is_admin != role_says_admin:
        raise ValueError("is_admin 与 role 不一致：is_admin 必须 = (role in {admin, super_admin})")


class PasswordEnvelopeFields(BaseModel):
    """口令密文随行的算法与密钥标识，字段名与登录请求保持一致。

    改密与登录提交的是同一类机密，客户端复用同一个 ``/auth/public-key``
    公钥，因此这里不另起一套字段名。
    """

    encryption: str | None = Field(default=None, max_length=50)
    key_id: str | None = Field(default=None, max_length=64)


class UserCreateRequest(PasswordEnvelopeFields):
    """用户创建请求。

    管理员建号时设的初始口令与登录/改密/重置是同一类机密，所以走同一套传输
    形态：明文字段可选，是否放行由 ``LOGIN_PASSWORD_ENCRYPTION_REQUIRED``
    在服务端判定，schema 不做策略。``password`` 的 8 位下限只对明文有意义
    ——密文长度是 RSA 块长，强度校验在解密后由 ``validate_password_strength``
    在 service 层统一执行。
    """

    username: str = Field(..., min_length=3, max_length=50)
    password: str | None = Field(default=None, min_length=8)
    encrypted_password: str | None = Field(default=None, min_length=1)
    email: str | None = Field(None, max_length=100)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    role: str | None = Field(None, max_length=20)

    @model_validator(mode="after")
    def validate_role_consistency(self) -> "UserCreateRequest":
        if not self.password and not self.encrypted_password:
            raise ValueError("password 或 encrypted_password 至少需要一个")
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


class UserPasswordUpdateRequest(PasswordEnvelopeFields):
    """用户密码更新请求。

    明文字段保留为可选，是为了让 ``LOGIN_PASSWORD_ENCRYPTION_REQUIRED=false``
    的部署仍能提交明文；是否放行由该开关在服务端判定，schema 不做策略。
    ``new_password`` 的 8 位下限只对明文有意义——密文长度是 RSA 块长，
    强度校验在解密后由 ``validate_password_strength`` 统一执行。
    """

    old_password: str | None = Field(default=None, min_length=1)
    new_password: str | None = Field(default=None, min_length=8)
    encrypted_old_password: str | None = Field(default=None, min_length=1)
    encrypted_new_password: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_password_presence(self) -> "UserPasswordUpdateRequest":
        if not self.old_password and not self.encrypted_old_password:
            raise ValueError("old_password 或 encrypted_old_password 至少需要一个")
        if not self.new_password and not self.encrypted_new_password:
            raise ValueError("new_password 或 encrypted_new_password 至少需要一个")
        return self


class UserAdminPasswordUpdateRequest(PasswordEnvelopeFields):
    """管理员重置密码请求"""

    new_password: str | None = Field(default=None, min_length=8)
    encrypted_new_password: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_password_presence(self) -> "UserAdminPasswordUpdateRequest":
        if not self.new_password and not self.encrypted_new_password:
            raise ValueError("new_password 或 encrypted_new_password 至少需要一个")
        return self


class UserBatchStatusRequest(BaseModel):
    """批量启停请求；状态只接受 JSON boolean，避免字符串真值歧义。"""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[StrictStr | StrictInt]
    is_active: StrictBool


class UserListQueryParams(BaseModel):
    """管理员用户列表的服务端筛选和分页参数。"""

    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
    search: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    is_admin: bool | None = None
    sort_by: Literal["id", "username", "created_at"] | None = None
    sort_order: Literal["asc", "desc"] | None = None


class UserResponse(BaseModel):
    """用户响应"""

    id: str = Field(
        ...,
        description="用户公开ID",
        validation_alias=AliasChoices("public_id", "id"),
    )
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

    @field_validator("email", mode="before")
    @classmethod
    def normalize_nullable_email(cls, value: str | None) -> str:
        return value or ""


class UserSimpleResponse(BaseModel):
    """用户简要响应"""

    id: str = Field(
        ...,
        description="用户公开ID",
        validation_alias=AliasChoices("public_id", "id"),
    )
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
    "UserPasswordUpdateRequest",
    "UserAdminPasswordUpdateRequest",
    "UserBatchStatusRequest",
    "UserListQueryParams",
    "UserResponse",
    "UserSimpleResponse",
    "UserListResponse",
    "UserLoginResponse",
    "LoginPublicKeyResponse",
]
