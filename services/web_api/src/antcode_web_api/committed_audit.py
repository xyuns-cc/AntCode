"""审计已提交业务操作，不得把审计故障伪装成业务失败。"""

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from antcode_web_api.prometheus_metrics import AUDIT_WRITE_FAILURES

AuditWrite = Callable[[], Awaitable[Any]]


async def record_committed_audit(operation: str, audit_write: AuditWrite) -> bool:
    """执行提交后的审计写入；失败必须显式告警，但不改变业务响应语义。"""
    try:
        await audit_write()
    except Exception as exc:
        AUDIT_WRITE_FAILURES.labels(operation=operation).inc()
        logger.opt(exception=exc).critical(
            "业务操作已提交但审计写入失败: operation={}",
            operation,
        )
        return False
    return True


__all__ = ["record_committed_audit"]
