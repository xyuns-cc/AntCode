from typing import Any, Generic, List, Sequence, TypeVar

from pydantic import BaseModel

from src.schemas.common import BaseResponse, PaginationInfo, PaginationResponse
from src.core.response_codes import ResponseCode
from src.core.messages import Messages

T = TypeVar('T')


def success(
    data: Any = None,
    message: str = Messages.OPERATION_SUCCESS,
    code: int | ResponseCode = ResponseCode.SUCCESS,
) -> BaseResponse[Any]:
    return BaseResponse[Any](
        success=True,
        code=int(code),
        message=message,
        data=data,
    )


def error(
    message: str,
    code: int | ResponseCode,
    data: Any = None,
) -> BaseResponse[Any]:
    return BaseResponse[Any](
        success=False,
        code=int(code),
        message=message,
        data=data,
    )


def page(
    items: Sequence[T],
    total: int,
    page: int,
    size: int,
    message: str = Messages.QUERY_SUCCESS,
    code: int | ResponseCode = ResponseCode.SUCCESS,
) -> PaginationResponse[T]:
    return PaginationResponse[T](
        success=True,
        code=int(code),
        message=message,
        data=list(items),
        pagination=PaginationInfo(page=page, size=size, total=total, pages=(total + size - 1) // size),
    )


def task_list(total: int, page_num: int, size: int, items: Sequence[Any]):
    """保持与现有前端兼容的任务列表响应构造器"""
    from src.schemas.scheduler import TaskListResponse
    return TaskListResponse(total=total, page=page_num, size=size, items=list(items))


def execution_list(total: int, page_num: int, size: int, items: Sequence[Any]):
    """保持与现有前端兼容的执行列表响应构造器"""
    from src.schemas.scheduler import ExecutionListResponse
    return ExecutionListResponse(total=total, page=page_num, size=size, items=list(items))
