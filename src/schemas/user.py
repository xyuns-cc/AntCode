"""
用户相关的Pydantic模式定义
包含用户创建、更新、响应等数据模式
"""

from datetime import datetime

from pydantic import BaseModel, Field


class UserLoginRequest(BaseModel):
    """用户登录请求模型"""
    username: str = Field(..., min_length=1, max_length=50, description="用户名")
    password: str = Field(..., min_length=1, max_length=100, description="密码")


class UserCreateRequest(BaseModel):
    """创建用户请求模型"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    password: str = Field(..., min_length=5, max_length=100, description="密码")
    email: str | None = Field(None, description="邮箱")
    is_active: bool = Field(True, description="是否激活")
    is_admin: bool = Field(False, description="是否管理员")


class UserUpdateRequest(BaseModel):
    """更新用户请求模型"""
    email: str | None = Field(None, description="邮箱")
    is_active: bool | None = Field(None, description="是否激活")
    is_admin: bool | None = Field(None, description="是否管理员")


class UserPasswordUpdateRequest(BaseModel):
    """用户密码更新请求模型"""
    old_password: str = Field(..., min_length=1, description="原密码")
    new_password: str = Field(..., min_length=6, max_length=100, description="新密码")


class UserAdminPasswordUpdateRequest(BaseModel):
    """管理员密码更新请求模型"""
    new_password: str = Field(..., min_length=6, max_length=100, description="新密码")


class UserResponse(BaseModel):
    """用户信息响应模型"""
    id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    email: str | None = Field(None, description="邮箱")
    is_active: bool = Field(..., description="是否激活")
    is_admin: bool = Field(..., description="是否管理员")
    created_at: datetime = Field(..., description="创建时间")
    last_login_at: datetime | None = Field(None, description="最后登录时间")

    class Config:
        from_attributes = True


class UserSimpleResponse(BaseModel):
    """用户简易信息响应模型"""
    id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    """用户列表响应模型"""
    id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    email: str | None = Field(None, description="邮箱")
    is_active: bool = Field(..., description="是否激活")
    is_admin: bool = Field(..., description="是否管理员")
    created_at: datetime = Field(..., description="创建时间")
    last_login_at: datetime | None = Field(None, description="最后登录时间")

    class Config:
        from_attributes = True


class UserLoginResponse(BaseModel):
    """用户登录响应模型"""
    access_token: str = Field(..., description="JWT访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    user_id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    is_admin: bool = Field(..., description="是否为管理员")
