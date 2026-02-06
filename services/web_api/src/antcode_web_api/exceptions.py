"""
Web API 异常模块

包含 HTTP 相关异常与响应处理，仅供 web_api 使用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from antcode_core.domain.schemas.common import ErrorDetail, ErrorResponse


class BusinessException(HTTPException):
    """业务异常基类"""

    def __init__(self, status_code: int, detail: str, error_code: str | None = None, errors: list | None = None):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code
        self.errors = errors or []


class ProjectNameExistsException(BusinessException):
    def __init__(self, name: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目名称 '{name}' 已存在",
            error_code="DUPLICATE_PROJECT_NAME",
        )


class ProjectNotFoundException(BusinessException):
    def __init__(self, project_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在",
            error_code="PROJECT_NOT_FOUND",
        )


class InvalidFileTypeException(BusinessException):
    def __init__(self, file_type: str, allowed_types: list[str]):
        super().__init__(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型 '{file_type}'，允许: {', '.join(allowed_types)}",
            error_code="INVALID_FILE_TYPE",
        )


class FileTooLargeException(BusinessException):
    def __init__(self, file_size: int, max_size: int):
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小 {file_size} 字节超过限制 {max_size} 字节",
            error_code="FILE_TOO_LARGE",
        )


class InvalidURLException(BusinessException):
    def __init__(self, url: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的URL: {url}",
            error_code="INVALID_URL",
        )


class InvalidSelectorException(BusinessException):
    def __init__(self, selector: str, selector_type: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 {selector_type} 选择器: {selector}",
            error_code="INVALID_SELECTOR",
        )


class StorageException(BusinessException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"存储错误: {detail}",
            error_code="STORAGE_ERROR",
        )


class RedisConnectionException(BusinessException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis连接失败: {detail}",
            error_code="REDIS_CONNECTION_ERROR",
        )


class TaskExecutionException(BusinessException):
    def __init__(self, detail: str, task_id: str | None = None):
        message = f"任务执行失败: {detail}"
        if task_id:
            message += f" (task_id: {task_id})"
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=message,
            error_code="TASK_EXECUTION_ERROR",
        )


class DatabaseConnectionException(BusinessException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"数据库连接失败: {detail}",
            error_code="DATABASE_CONNECTION_ERROR",
        )


class JSONParseException(BusinessException):
    def __init__(self, field_name: str, detail: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 格式错误: {detail}",
            error_code="JSON_PARSE_ERROR",
        )


def create_error_response(
    status_code: int,
    message: str,
    errors: list[dict[str, Any]] | list[ErrorDetail] | None = None,
    error_code: str | None = None,
) -> JSONResponse:
    """创建统一的错误响应"""
    error_details: list[ErrorDetail] = []
    if errors:
        for err in errors:
            if isinstance(err, dict):
                error_details.append(
                    ErrorDetail(
                        field=err.get("field", ""),
                        message=err.get("message", str(err)),
                    )
                )
            elif isinstance(err, ErrorDetail):
                error_details.append(err)

    resp = ErrorResponse(
        success=False,
        code=status_code,
        message=message,
        errors=error_details,
        timestamp=datetime.now(),
    )
    content = resp.model_dump()
    if isinstance(content.get("timestamp"), datetime):
        content["timestamp"] = content["timestamp"].isoformat()
    if error_code:
        content["error_code"] = error_code
    return JSONResponse(status_code=status_code, content=content)


async def business_exception_handler(request, exc: BusinessException) -> JSONResponse:
    """处理业务异常"""
    return create_error_response(
        status_code=exc.status_code,
        message=exc.detail,
        errors=getattr(exc, "errors", None),
        error_code=getattr(exc, "error_code", None),
    )


async def http_exception_handler(request, exc: HTTPException) -> JSONResponse:
    """处理 HTTP 异常"""
    return create_error_response(status_code=exc.status_code, message=exc.detail)


async def validation_exception_handler(request, exc) -> JSONResponse:
    """处理请求验证异常"""
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error.get("loc", []))
        errors.append(
            {
                "field": field,
                "message": error.get("msg", "验证失败"),
            }
        )
    return create_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        message="请求参数验证失败",
        errors=errors,
    )


async def general_exception_handler(request, exc: Exception) -> JSONResponse:
    """处理未捕获的异常"""
    from loguru import logger

    logger.exception("未处理异常: {}", exc)
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="服务器内部错误",
    )
