"""Worker dispatch failure conversion that preserves fencing errors."""

from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger

from antcode_core.application.services import lease_fenced_ready_publish as ready_publish
from antcode_core.application.services.workers.dispatch_error_codes import DISPATCH_UNEXPECTED_ERROR
from antcode_core.common.error_messages import normalize_persisted_error_message

DispatchResultT = TypeVar("DispatchResultT")


def failed_batch_dispatch(
    worker: Any,
    error: Exception,
    result_factory: Callable[..., DispatchResultT],
) -> DispatchResultT:
    ready_publish.raise_if_dispatch_fenced(error)
    logger.exception(f"批量任务分发失败 [{worker.name}]")
    # 走到这里的都是派发链路上没预料到的异常（含能力/Lease 在入队前变化）。它们不是容量
    # 不足，调用方重试不一定有用，必须与容量类分开——所以打的是 UNEXPECTED 而不是容量码。
    return result_factory(
        success=False,
        error=normalize_persisted_error_message(error),
        error_code=DISPATCH_UNEXPECTED_ERROR,
        worker_id=worker.public_id,
        worker_name=worker.name,
    )
