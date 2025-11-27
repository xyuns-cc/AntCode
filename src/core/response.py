"""响应工具"""
from enum import IntEnum

from src.schemas.common import BaseResponse, PaginationInfo, PaginationResponse
from src.schemas.scheduler import TaskListResponse, ExecutionListResponse


class ResponseCode(IntEnum):
    SUCCESS = 200
    CREATED = 201
    ACCEPTED = 202

    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    UNPROCESSABLE = 422

    SERVER_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503


class Messages:
    OPERATION_SUCCESS = "操作成功"
    CREATED_SUCCESS = "创建成功"
    UPDATED_SUCCESS = "更新成功"
    DELETED_SUCCESS = "删除成功"
    QUERY_SUCCESS = "查询成功"

    LOGIN_SUCCESS = "登录成功"
    LOGOUT_SUCCESS = "退出成功"
    UNAUTHORIZED = "未授权或会话已过期"
    FORBIDDEN = "权限不足"

    BAD_REQUEST = "请求参数无效"
    NOT_FOUND = "资源不存在"
    CONFLICT = "资源冲突"
    SERVER_ERROR = "服务器内部错误"


def success(data=None, message=Messages.OPERATION_SUCCESS, code=ResponseCode.SUCCESS):
    return BaseResponse(
        success=True,
        code=int(code),
        message=message,
        data=data,
    )


def error(message, code, data=None):
    return BaseResponse(
        success=False,
        code=int(code),
        message=message,
        data=data,
    )


def page(items, total, page, size, message=Messages.QUERY_SUCCESS, code=ResponseCode.SUCCESS):
    return PaginationResponse(
        success=True,
        code=int(code),
        message=message,
        data=list(items),
        pagination=PaginationInfo(page=page, size=size, total=total, pages=(total + size - 1) // size),
    )


def task_list(total, page_num, size, items):
    return TaskListResponse(total=total, page=page_num, size=size, items=list(items))


def execution_list(total, page_num, size, items):
    return ExecutionListResponse(total=total, page=page_num, size=size, items=list(items))
