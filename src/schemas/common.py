"""
通用响应模式定义
包含基础响应格式、错误响应、分页等通用模式
"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar('T')

class BaseResponse(BaseModel, Generic[T]):
    """基础响应格式"""
    success: bool = Field(description="请求是否成功")
    code: int = Field(description="状态码")
    message: str = Field(description="响应消息")
    data: T | None = Field(None, description="响应数据")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")


class ErrorDetail(BaseModel):
    """错误详情"""
    field: str = Field(description="错误字段")
    message: str = Field(description="错误消息")


class ErrorResponse(BaseModel):
    """错误响应格式"""
    success: bool = Field(False, description="请求是否成功")
    code: int = Field(description="错误状态码")
    message: str = Field(description="错误消息")
    errors: list[ErrorDetail] | None = Field(None, description="详细错误信息")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(1, ge=1, description="页码")
    size: int = Field(20, ge=1, le=100, description="每页数量")


class PaginationInfo(BaseModel):
    """分页信息"""
    page: int = Field(description="当前页码")
    size: int = Field(description="每页数量")
    total: int = Field(description="总记录数")
    pages: int = Field(description="总页数")


class PaginationResponse(BaseModel, Generic[T]):
    """分页响应格式"""
    success: bool = Field(True, description="请求是否成功")
    code: int = Field(200, description="状态码")
    message: str = Field("查询成功", description="响应消息")
    data: list[T] = Field(description="数据列表")
    pagination: PaginationInfo = Field(description="分页信息")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")
