"""用户数据模式"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=100)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    email: str | None = None
    is_active: bool = True
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    email: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class UserPasswordUpdateRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=100)


class UserAdminPasswordUpdateRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=100)


class UserResponse(BaseModel):
    id: str = Field(description="用户公开ID")
    username: str
    email: str | None = None
    is_active: bool
    is_admin: bool
    is_super_admin: bool = False
    is_online: bool = False
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UserSimpleResponse(BaseModel):
    id: str = Field(description="用户公开ID")
    username: str

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    id: str = Field(description="用户公开ID")
    username: str
    email: str | None = None
    is_active: bool
    is_admin: bool
    is_super_admin: bool = False
    is_online: bool = False
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UserLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str = Field(description="用户公开ID")
    username: str
    is_admin: bool
    is_super_admin: bool = False
