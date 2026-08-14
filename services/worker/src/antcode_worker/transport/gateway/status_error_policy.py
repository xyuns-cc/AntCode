"""gRPC status error classification for Gateway Worker settlement."""

from __future__ import annotations

from typing import Any

from loguru import logger

from antcode_worker.transport.base import TaskResult


def is_permanent_control_result_error(error: Exception) -> bool:
    import grpc

    code = error.code() if hasattr(error, "code") else None
    return code in {
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNIMPLEMENTED,
    }


def is_permanent_status_error(error: Exception) -> bool:
    import grpc

    code = error.code() if hasattr(error, "code") else None
    return code in {
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.PERMISSION_DENIED,
    }


def is_lease_fence_error(error: Exception) -> bool:
    import grpc

    code = error.code() if hasattr(error, "code") else None
    return code == grpc.StatusCode.FAILED_PRECONDITION


class GatewayStatusErrorPolicy:
    """Mixin that applies settlement-specific reconnect and fencing policy."""

    _consecutive_failures: int

    async def _abort_lease_revocation(self) -> None: ...

    async def _handle_connection_error(self, error: Exception) -> None: ...

    async def _handle_status_report_error(self, result: TaskResult, error: Exception) -> bool:
        if is_lease_fence_error(error):
            logger.error(f"report_result 被 Gateway 代际围栏拒绝: task_id={result.task_id}")
            await self._abort_lease_revocation()
            return False
        if is_permanent_status_error(error):
            logger.error(
                "report_result 被 Gateway 永久拒绝: task_id={} code={} details={}",
                result.task_id,
                _error_value(error, "code", "unknown"),
                _error_value(error, "details", str(error)),
            )
            return False
        self._consecutive_failures += 1
        logger.warning(f"report_result StreamStatus 失败: task_id={result.task_id} exc={error}")
        await self._handle_connection_error(error)
        return False


def _error_value(error: Exception, attribute: str, default: Any) -> Any:
    getter = getattr(error, attribute, None)
    return getter() if callable(getter) else default


__all__ = [
    "GatewayStatusErrorPolicy",
    "is_lease_fence_error",
    "is_permanent_control_result_error",
]
