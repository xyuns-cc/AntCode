"""
异常工具

Requirements: 13.4
"""

import asyncio

from antcode_worker.domain.errors import (
    ExecutionError,
    ResourceLimitError,
    TimeoutError,
    TransportError,
    WorkerError,
)


def map_exception(e: Exception) -> WorkerError:
    """将标准异常映射为 WorkerError"""
    if isinstance(e, WorkerError):
        return e

    if isinstance(e, asyncio.TimeoutError):
        return TimeoutError(str(e))

    if isinstance(e, ConnectionError):
        return TransportError(str(e), retryable=True)

    if isinstance(e, MemoryError):
        return ResourceLimitError(str(e), resource_type="memory")

    return ExecutionError(str(e))


def is_retryable(e: Exception) -> bool:
    """判断异常是否可重试"""
    if isinstance(e, TransportError):
        return e.retryable
    return isinstance(e, (ConnectionError, asyncio.TimeoutError))


def get_error_code(e: Exception) -> str:
    """获取错误码"""
    if isinstance(e, WorkerError):
        return e.code
    return "UNKNOWN_ERROR"
