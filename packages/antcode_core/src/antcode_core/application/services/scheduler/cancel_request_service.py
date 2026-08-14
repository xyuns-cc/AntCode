"""已分配 TaskRun 的持久化取消请求。"""

from __future__ import annotations

from datetime import UTC, datetime

from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import TaskStatus

_CANCEL_REQUESTABLE_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.DISPATCHING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
)


async def record_cancel_request(
    run_id: str,
    *,
    requested_by: int | None,
    requested_at: datetime | None = None,
) -> bool:
    """原子记录首次取消请求；重复请求保持原始审计信息并允许重新投递。"""
    timestamp = requested_at or datetime.now(UTC)
    updated = await TaskRun.filter(
        run_id=run_id,
        status__in=_CANCEL_REQUESTABLE_STATUSES,
        cancel_requested_at__isnull=True,
    ).update(
        cancel_requested_at=timestamp,
        cancel_requested_by=requested_by,
    )
    if updated == 1:
        return True
    return await TaskRun.filter(
        run_id=run_id,
        status__in=_CANCEL_REQUESTABLE_STATUSES,
        cancel_requested_at__isnull=False,
    ).exists()


__all__ = ["record_cancel_request"]
