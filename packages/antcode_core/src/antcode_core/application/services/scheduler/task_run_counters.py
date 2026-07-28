"""Authoritative Task outcome counters derived from TaskRun rows."""

from typing import Any

from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun

FAILURE_STATUSES = (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.REJECTED)


async def task_run_outcome_counts(connection: Any, task_id: int) -> dict[str, int]:
    success_count = await TaskRun.filter(task_id=task_id, status=TaskStatus.SUCCESS).using_db(connection).count()
    failure_count = await TaskRun.filter(task_id=task_id, status__in=FAILURE_STATUSES).using_db(connection).count()
    return {"success_count": success_count, "failure_count": failure_count}


__all__ = ["FAILURE_STATUSES", "task_run_outcome_counts"]
