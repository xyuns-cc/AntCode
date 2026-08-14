"""Retry-queue cleanup driven by durable task deletion events."""

from loguru import logger


async def cleanup_deleted_task_retries(task_id: int) -> int:
    from antcode_master.control.retry_loop import retry_service

    removed = await retry_service.cancel_task(task_id)
    if removed:
        logger.info("已清理被删除任务的 retry ZSet: task_id={} removed={}", task_id, removed)
    return removed


__all__ = ["cleanup_deleted_task_retries"]
