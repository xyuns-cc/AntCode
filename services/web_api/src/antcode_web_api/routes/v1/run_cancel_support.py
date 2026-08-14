"""运行取消路由的持久化、发送与响应辅助。"""

from collections.abc import Awaitable, Callable

from antcode_core.application.services.scheduler.cancel_request_service import record_cancel_request
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.schemas.common import BaseResponse
from loguru import logger

from antcode_web_api.response import success as success_response

CancelEventWriter = Callable[[TaskRun, int], Awaitable[None]]


async def record_assigned_cancel_request(run_id: str, user_id: int) -> bool:
    return await record_cancel_request(run_id, requested_by=user_id)


async def send_worker_cancel(execution: TaskRun, user_id: int, write_event: CancelEventWriter) -> bool:
    if not execution.worker_id:
        return False
    try:
        await write_event(execution, user_id)
        return True
    except Exception:
        logger.exception("发送取消指令失败: run_id={} worker_id={}", execution.run_id, execution.worker_id)
        return False


def cancel_success(run_id: str, *, remote_cancelled: bool) -> BaseResponse[dict]:
    result_status = "cancel_requested" if remote_cancelled else "cancelled"
    message = "取消请求已受理" if remote_cancelled else "任务已取消"
    return success_response(
        {"run_id": run_id, "status": result_status, "remote_cancelled": remote_cancelled},
        message=message,
    )
