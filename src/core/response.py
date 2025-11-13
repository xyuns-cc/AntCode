"""
统一响应处理模块
包含响应码、消息和响应构造器
"""
from enum import IntEnum

from pydantic import BaseModel

from src.schemas.common import BaseResponse, PaginationInfo, PaginationResponse

T = type  # 简化类型变量


# ==================== 响应码 ====================
class ResponseCode(IntEnum):
    """HTTP响应状态码"""
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


# ==================== 响应消息 ====================
class Messages:
    """标准响应消息"""
    # 通用
    OPERATION_SUCCESS = "操作成功"
    CREATED_SUCCESS = "创建成功"
    UPDATED_SUCCESS = "更新成功"
    DELETED_SUCCESS = "删除成功"
    QUERY_SUCCESS = "查询成功"

    # 认证
    LOGIN_SUCCESS = "登录成功"
    LOGOUT_SUCCESS = "退出登录成功"
    UNAUTHORIZED = "未认证或登录已过期"
    FORBIDDEN = "权限不足"

    # 错误
    BAD_REQUEST = "请求参数错误"
    NOT_FOUND = "资源不存在"
    CONFLICT = "资源冲突"
    SERVER_ERROR = "服务器内部错误"


# ==================== 响应构造器 ====================
def success(
    data = None,
    message = Messages.OPERATION_SUCCESS,
    code = ResponseCode.SUCCESS,
):
    """成功响应"""
    return BaseResponse(
        success=True,
        code=int(code),
        message=message,
        data=data,
    )


def error(
    message,
    code,
    data = None,
):
    """错误响应"""
    return BaseResponse(
        success=False,
        code=int(code),
        message=message,
        data=data,
    )


def page(
    items,
    total,
    page,
    size,
    message = Messages.QUERY_SUCCESS,
    code = ResponseCode.SUCCESS,
):
    """分页响应"""
    return PaginationResponse(
        success=True,
        code=int(code),
        message=message,
        data=list(items),
        pagination=PaginationInfo(page=page, size=size, total=total, pages=(total + size - 1) // size),
    )


def task_list(total, page_num, size, items):
    """任务列表响应（保持与前端兼容）"""
    from src.schemas.scheduler import TaskListResponse
    return TaskListResponse(total=total, page=page_num, size=size, items=list(items))


def execution_list(total, page_num, size, items):
    """执行列表响应（保持与前端兼容）"""
    from src.schemas.scheduler import ExecutionListResponse
    return ExecutionListResponse(total=total, page=page_num, size=size, items=list(items))
