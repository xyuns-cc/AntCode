"""通用响应模式"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar('T')


class BaseResponse(BaseModel, Generic[T]):
    success: bool = Field(default=True)
    code: int = Field(default=200)
    message: str = Field(default="")
    data: T | None = Field(default=None)
    timestamp: datetime = Field(default_factory=datetime.now)


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    success: bool = Field(default=False)
    code: int
    message: str
    errors: list[ErrorDetail] | None = Field(default=None)
    timestamp: datetime = Field(default_factory=datetime.now)


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


class PaginationInfo(BaseModel):
    page: int
    size: int
    total: int
    pages: int


class PaginationResponse(BaseModel, Generic[T]):
    success: bool = Field(default=True)
    code: int = Field(default=200)
    message: str = Field(default="Query successful")
    data: list[T]
    pagination: PaginationInfo
    timestamp: datetime = Field(default_factory=datetime.now)
