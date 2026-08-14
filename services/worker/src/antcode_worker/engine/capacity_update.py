"""Transactional live capacity updates for Worker components."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


async def apply_capacity_limits(
    scheduler: Any,
    executor: Any,
    observer: Callable[[int], None] | None,
    *,
    previous: int,
    target: int,
) -> None:
    """Update all capacity owners, rolling back before surfacing a failure."""
    try:
        await scheduler.update_max_size(target * 2)
        await executor.resize_concurrency(target)
        if observer is not None:
            observer(target)
    except Exception as primary:
        rollback_failures = await _rollback_capacity(
            scheduler,
            executor,
            observer,
            previous=previous,
        )
        if rollback_failures:
            raise ExceptionGroup(
                "Worker 容量更新失败且回滚不完整",
                [primary, *rollback_failures],
            ) from primary
        raise


async def _rollback_capacity(
    scheduler: Any,
    executor: Any,
    observer: Callable[[int], None] | None,
    *,
    previous: int,
) -> list[Exception]:
    failures: list[Exception] = []
    for operation in (
        lambda: scheduler.update_max_size(previous * 2),
        lambda: executor.resize_concurrency(previous),
    ):
        try:
            await operation()
        except Exception as exc:
            failures.append(exc)
    if observer is not None:
        try:
            observer(previous)
        except Exception as exc:
            failures.append(exc)
    return failures


__all__ = ["apply_capacity_limits"]
