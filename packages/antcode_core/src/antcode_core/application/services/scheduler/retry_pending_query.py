"""PostgreSQL-authoritative reads for durable retry intents."""

from antcode_core.domain.models.task_run import TaskRun


async def list_durable_pending_retries() -> list[dict]:
    executions = await (
        TaskRun.filter(next_retry_at__not_isnull=True)
        .order_by("next_retry_at", "id")
        .only("task_id", "run_id", "retry_count", "next_retry_at")
    )
    return [
        {
            "task_id": execution.task_id,
            "run_id": execution.run_id,
            "retry_time": execution.next_retry_at.isoformat(),
            "retry_count": execution.retry_count,
        }
        for execution in executions
    ]


__all__ = ["list_durable_pending_retries"]
