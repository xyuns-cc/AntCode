"""
Web API 异常模块

包含 HTTP 相关异常与响应处理，仅供 web_api 使用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from antcode_core.domain.schemas.common import ErrorData, ErrorDetail, ErrorResponse
from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class BusinessException(HTTPException):
    """业务异常基类"""

    def __init__(self, status_code: int, detail: str, error_code: str | None = None, errors: list | None = None):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code
        self.errors = errors or []


class ProjectNotFoundException(BusinessException):
    def __init__(self, project_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在",
            error_code="PROJECT_NOT_FOUND",
        )


def create_error_response(
    status_code: int,
    message: str,
    errors: list[dict[str, Any]] | list[ErrorDetail] | None = None,
    error_code: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
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

    error_data = None
    if error_code or error_details:
        error_data = ErrorData(error_code=error_code, errors=error_details)

    resp = ErrorResponse(
        success=False,
        code=status_code,
        message=message,
        data=error_data,
    )
    content = resp.model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=content, headers=headers)


def _validation_errors_from_detail(detail: Any) -> list[dict[str, str]]:
    if not isinstance(detail, list):
        return []
    errors: list[dict[str, str]] = []
    for item in detail:
        if not isinstance(item, dict):
            errors.append({"field": "", "message": str(item)})
            continue
        loc = item.get("loc", "")
        field = ".".join(str(part) for part in loc) if isinstance(loc, (list, tuple)) else str(loc)
        errors.append({"field": field, "message": str(item.get("msg", item))})
    return errors


def _http_error_message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return "请求参数验证失败"
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        return str(message) if message is not None else str(detail)
    return str(detail)


async def business_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理业务异常"""
    if not isinstance(exc, BusinessException):
        raise TypeError("business_exception_handler requires BusinessException")
    return create_error_response(
        status_code=exc.status_code,
        message=exc.detail,
        errors=getattr(exc, "errors", None),
        error_code=getattr(exc, "error_code", None),
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """处理 HTTP 异常"""
    if not isinstance(exc, (HTTPException, StarletteHTTPException)):
        raise TypeError("http_exception_handler requires HTTPException")
    return create_error_response(
        status_code=exc.status_code,
        message=_http_error_message(exc.detail),
        errors=_validation_errors_from_detail(exc.detail),
        # 与 business_exception_handler 对齐：非 web_api 层（如 antcode_core 的
        # RuntimeControlFailure）抛出的 HTTPException 也能把结构化码带到响应体。
        error_code=getattr(exc, "error_code", None),
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理请求验证异常"""
    if not isinstance(exc, RequestValidationError):
        raise TypeError("validation_exception_handler requires RequestValidationError")
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
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        message="请求参数验证失败",
        errors=errors,
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理未捕获的异常"""
    from loguru import logger

    logger.exception("未处理异常: {}", exc)
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="服务器内部错误",
    )
