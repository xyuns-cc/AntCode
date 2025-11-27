"""异常处理"""

import traceback
from datetime import datetime

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from src.schemas.common import BaseResponse


class BusinessException(HTTPException):
    """业务异常基类"""
    
    def __init__(self, status_code, detail, error_code=None, errors=None):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code
        self.errors = errors or []


class ProjectNameExistsException(BusinessException):
    def __init__(self, name):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目名称 '{name}' 已存在",
            error_code="DUPLICATE_PROJECT_NAME"
        )


class ProjectNotFoundException(BusinessException):
    def __init__(self, project_id):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在",
            error_code="PROJECT_NOT_FOUND"
        )


class InvalidFileTypeException(BusinessException):
    def __init__(self, file_type, allowed_types):
        super().__init__(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型 '{file_type}'，允许: {', '.join(allowed_types)}",
            error_code="INVALID_FILE_TYPE"
        )


class FileTooLargeException(BusinessException):
    def __init__(self, file_size, max_size):
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小 {file_size} 字节超过限制 {max_size} 字节",
            error_code="FILE_TOO_LARGE"
        )


class InvalidURLException(BusinessException):
    def __init__(self, url):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的URL: {url}",
            error_code="INVALID_URL"
        )


class InvalidSelectorException(BusinessException):
    def __init__(self, selector, selector_type):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 {selector_type} 选择器: {selector}",
            error_code="INVALID_SELECTOR"
        )


class StorageException(BusinessException):
    def __init__(self, detail):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"存储错误: {detail}",
            error_code="STORAGE_ERROR"
        )


class RedisConnectionException(BusinessException):
    def __init__(self, detail):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis连接失败: {detail}",
            error_code="REDIS_CONNECTION_ERROR"
        )


class TaskExecutionException(BusinessException):
    def __init__(self, detail, task_id=None):
        message = f"任务执行失败: {detail}"
        if task_id:
            message += f" (task_id: {task_id})"
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=message,
            error_code="TASK_EXECUTION_ERROR"
        )


class DatabaseConnectionException(BusinessException):
    def __init__(self, detail):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"数据库连接失败: {detail}",
            error_code="DATABASE_CONNECTION_ERROR"
        )


class JSONParseException(BusinessException):
    def __init__(self, field_name, detail):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 格式错误: {detail}",
            error_code="JSON_PARSE_ERROR"
        )


def create_error_response(status_code, message):
    resp = BaseResponse(
        success=False,
        code=status_code,
        message=message,
        data=None,
        timestamp=datetime.now(),
    )
    content = resp.model_dump()
    if isinstance(content.get("timestamp"), datetime):
        content["timestamp"] = content["timestamp"].isoformat()
    return JSONResponse(status_code=status_code, content=content)


async def business_exception_handler(request, exc):
    return create_error_response(status_code=exc.status_code, message=exc.detail)


async def http_exception_handler(request, exc):
    return create_error_response(status_code=exc.status_code, message=exc.detail)


async def validation_exception_handler(request, exc):
    return create_error_response(status_code=status.HTTP_400_BAD_REQUEST, message="请求参数验证失败")


async def general_exception_handler(request, exc):
    print(f"未处理异常: {exc}")
    print(traceback.format_exc())
    
    return create_error_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message="服务器内部错误")


ERROR_CODE_MAPPING = {
    "INVALID_PROJECT_NAME": "无效的项目名称",
    "DUPLICATE_PROJECT_NAME": "项目名称已存在",
    "PROJECT_NOT_FOUND": "项目不存在",
    "INVALID_FILE_TYPE": "不支持的文件类型",
    "FILE_TOO_LARGE": "文件大小超过限制",
    "INVALID_DEPENDENCIES": "无效的依赖格式",
    "INVALID_URL": "无效的URL格式",
    "INVALID_SELECTOR": "无效的选择器语法",
    "STORAGE_ERROR": "存储操作失败",
}
