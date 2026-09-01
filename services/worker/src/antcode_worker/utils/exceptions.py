"""
异常工具

Requirements: 13.4
"""

import asyncio

from antcode_worker.domain.errors import (
    TransportError,
)


def is_retryable(e: Exception) -> bool:
    """判断异常是否可重试"""
    if isinstance(e, TransportError):
        return e.retryable
    return isinstance(e, (ConnectionError, asyncio.TimeoutError))
