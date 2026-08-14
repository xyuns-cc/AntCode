"""Worker dispatch failure conversion that preserves fencing errors."""

from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger

from antcode_core.application.services import lease_fenced_ready_publish as ready_publish
from antcode_core.common.error_messages import normalize_persisted_error_message

DispatchResultT = TypeVar("DispatchResultT")


def failed_batch_dispatch(
    worker: Any,
    error: Exception,
    result_factory: Callable[..., DispatchResultT],
) -> DispatchResultT:
    ready_publish.raise_if_dispatch_fenced(error)
    logger.exception(f"批量任务分发失败 [{worker.name}]")
    return result_factory(
        success=False,
        error=normalize_persisted_error_message(error),
        worker_id=worker.public_id,
        worker_name=worker.name,
    )
