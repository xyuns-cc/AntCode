"""
通用 Schema

通用响应模式和分页参数。
"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class BaseResponse(BaseModel, Generic[T]):
    """通用响应模型"""
    success: bool = Field(default=True)
    code: int = Field(default=200)
    message: str = Field(default="")
    data: T | None = Field(default=None)
    timestamp: datetime = Field(default_factory=datetime.now)


class ErrorDetail(BaseModel):
    """错误详情"""
    field: str
    message: str


class ErrorData(BaseModel):
    """错误扩展数据"""

    error_code: str | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseResponse[ErrorData | None]):
    """错误响应模型"""
    success: bool = Field(default=False)
    data: ErrorData | None = None


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


class PaginationInfo(BaseModel):
    """分页信息"""
    page: int
    size: int
    total: int
    pages: int


class PaginationData(BaseModel, Generic[T]):
    """分页数据体"""

    items: list[T]
    pagination: PaginationInfo


class PaginationResponse(BaseResponse[PaginationData[T]], Generic[T]):
    """分页响应模型"""
    message: str = Field(default="Query successful")
    data: PaginationData[T]


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    version: str
    timestamp: str


class AppInfoResponse(BaseModel):
    """应用信息响应"""
    name: str
    title: str
    version: str
    description: str = ""
    copyright_year: str = "2025"


__all__ = [
    "BaseResponse",
    "ErrorDetail",
    "ErrorData",
    "ErrorResponse",
    "PaginationParams",
    "PaginationInfo",
    "PaginationData",
    "PaginationResponse",
    "HealthResponse",
    "AppInfoResponse",
]
