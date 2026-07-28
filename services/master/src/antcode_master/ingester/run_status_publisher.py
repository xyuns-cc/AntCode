"""Publish the persisted TaskRun state to realtime SSE consumers."""

from antcode_core.application.services.workers.distributed_log_status import (
    display_status_message,
    status_progress,
)
from antcode_core.common.realtime_events import build_run_status_message
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.infrastructure.redis.sse_event_stream import publish_sse_event


async def publish_persisted_run_status(run_id: str) -> None:
    """Publish the final database state, never the possibly stale input state."""
    execution = await TaskRun.filter(run_id=run_id).only("status", "error_message").first()
    if execution is None:
        raise RuntimeError(f"结果更新后执行记录消失: run_id={run_id}")
    status = execution.status.value
    status_data = {
        "status": status,
        "error_message": execution.error_message,
    }
    await publish_sse_event(
        build_run_status_message(
            run_id,
            status=status,
            progress=status_progress(status),
            message=display_status_message(status_data),
        )
    )


__all__ = ["publish_persisted_run_status"]
