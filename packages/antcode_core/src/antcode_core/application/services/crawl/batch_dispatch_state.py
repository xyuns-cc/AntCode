"""Durable identity and state markers for Crawl seed dispatch."""

from __future__ import annotations

import uuid

from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus

_RUN_NAMESPACE = uuid.UUID("f431119c-e8f1-4778-afef-b70f680bf90c")


def crawl_batch_run_id(batch_id: str, seed_url: str) -> str:
    identity = f"{batch_id}\0{seed_url}"
    return f"batch-{uuid.uuid5(_RUN_NAMESPACE, identity).hex}"


def is_recoverable_dispatch(run: TaskRun) -> bool:
    return (
        run.status == TaskStatus.PENDING
        and run.runtime_status is None
        and run.dispatch_status in (DispatchStatus.PENDING, DispatchStatus.FAILED)
    )


async def mark_dispatch_succeeded(run_id: str) -> None:
    await TaskRun.filter(
        run_id=run_id,
        status=TaskStatus.PENDING,
        runtime_status__isnull=True,
    ).update(status=TaskStatus.QUEUED, dispatch_status=DispatchStatus.DISPATCHED)


async def mark_redispatch_enqueued(run_id: str) -> None:
    await TaskRun.filter(
        run_id=run_id,
        status=TaskStatus.PENDING,
        runtime_status__isnull=True,
    ).update(dispatch_status=DispatchStatus.DISPATCHING)


__all__ = [
    "crawl_batch_run_id",
    "is_recoverable_dispatch",
    "mark_dispatch_succeeded",
    "mark_redispatch_enqueued",
]
