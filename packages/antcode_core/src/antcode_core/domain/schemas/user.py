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


class UserCreateRequest(BaseModel):
    """用户创建请求"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    email: str | None = Field(None, max_length=100)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)


class UserUpdateRequest(BaseModel):
    """用户更新请求"""
    email: str | None = Field(None, max_length=100)
    is_active: bool | None = None
    is_admin: bool | None = None


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
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None

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
    "UserPasswordUpdateRequest",
    "UserAdminPasswordUpdateRequest",
    "UserResponse",
    "UserSimpleResponse",
    "UserListResponse",
    "UserLoginResponse",
    "LoginPublicKeyResponse",
]
