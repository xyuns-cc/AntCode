"""Prevent run deletion until the executing Worker releases ownership."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from antcode_core.application.services.workers.run_ownership_fence import run_owner_key
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.infrastructure.redis import get_redis_client

OWNERSHIP_LOOKUP_BATCH_SIZE = 200
TASK_RUN_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.SKIPPED,
        TaskStatus.REJECTED,
    }
)


class RunSettlementPendingError(ValueError):
    """At least one terminal run is still settling on a Worker."""


class RunSettlementGuardUnavailable(RuntimeError):
    """The ownership store could not provide an authoritative answer."""


def _normalize_run_ids(run_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(run_id) for run_id in run_ids if run_id))


async def ensure_runs_settled(
    run_ids: Iterable[str],
    *,
    redis_client: Any | None = None,
) -> None:
    """Fail closed while any run ownership key still exists."""
    normalized = _normalize_run_ids(run_ids)
    if not normalized:
        return
    try:
        redis = redis_client if redis_client is not None else await get_redis_client()
    except Exception as exc:
        raise RunSettlementGuardUnavailable("执行结算状态服务不可用") from exc
    for offset in range(0, len(normalized), OWNERSHIP_LOOKUP_BATCH_SIZE):
        batch = normalized[offset : offset + OWNERSHIP_LOOKUP_BATCH_SIZE]
        try:
            owners = await redis.mget([run_owner_key(run_id) for run_id in batch])
            if len(owners) != len(batch):
                raise RuntimeError("Redis MGET 返回数量不匹配")
        except Exception as exc:
            raise RunSettlementGuardUnavailable("执行结算状态服务不可用") from exc
        pending = [run_id for run_id, owner in zip(batch, owners, strict=True) if owner is not None]
        if pending:
            raise RunSettlementPendingError("执行结果仍在结算，请等待 Worker 完成确认后再删除")


async def load_deletable_run_ids(connection: Any, task_ids: Iterable[int]) -> list[str]:
    """Load terminal runs and verify ownership while caller holds Task locks."""
    from antcode_core.domain.models.task_run import TaskRun

    normalized_tasks = tuple(dict.fromkeys(task_ids))
    if not normalized_tasks:
        return []
    runs = TaskRun.filter(task_id__in=list(normalized_tasks)).using_db(connection)
    if await runs.exclude(status__in=list(TASK_RUN_TERMINAL_STATUSES)).exists():
        raise ValueError("任务存在未终态执行，请先取消并等待执行结束")
    rows = await runs.only("run_id").all()
    run_ids = [row.run_id for row in rows if row.run_id]
    await ensure_runs_settled(run_ids)
    return run_ids


__all__ = [
    "TASK_RUN_TERMINAL_STATUSES",
    "RunSettlementGuardUnavailable",
    "RunSettlementPendingError",
    "ensure_runs_settled",
    "load_deletable_run_ids",
]
