"""Worker 到 TaskRun 的可信归属校验。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from tortoise.transactions import in_transaction

from antcode_core.application.services.workers.log_ingest_generation import parse_stream_id
from antcode_core.application.services.workers.spider_run_access import (
    StaleSpiderLeaseError,
    verify_spider_project_binding,
)
from antcode_core.domain.models import TaskRun, TaskRunLeaseGeneration, Worker
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_status_sets import SPIDER_WRITABLE_TASK_STATUSES

# Lease ID 契约上限（与 Gateway/Lua 侧一致）。
MAX_LEASE_ID_LENGTH = 64

# P1-GW-02 (round6): TaskRun 终态集合。ownership claim/bind 遇到这些状态一律拒,
# 防"已完成 run 的 ACK 丢失后 L2 重新 claim 造成重复执行"。审查场景:
# - L1 完成任务, ReportResult 成功持久化 TaskStatus=SUCCESS
# - L1 网络抖动, AckTask 丢失, PEL 未 XACK
# - Master reconcile 判 L1 死, L2 XAUTOCLAIM 拿到消息
# - L2 调 ClaimRunOwnership, 若不查终态就 acquired=True, 启动子进程重复
#   跑该 run 的外部副作用(付款/文件上传/邮件); 迟到 SUCCESS 的终态吸收
#   规则不能撤销已产生的物理副作用。
_TASK_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.SKIPPED,
        TaskStatus.REJECTED,
    }
)
_TASK_CLAIMABLE_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.DISPATCHING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    }
)


async def _resolve_worker(worker: Worker | str) -> Worker:
    resolved = await Worker.get_or_none(public_id=worker) if isinstance(worker, str) else worker
    if resolved is None:
        raise PermissionError("Worker 不存在")
    return resolved


async def require_worker_owns_runs(
    worker: Worker | str,
    run_ids: Iterable[str],
) -> None:
    normalized = {run_id.strip() for run_id in run_ids if run_id and run_id.strip()}
    if not normalized:
        raise ValueError("run_id 不能为空")

    resolved = await _resolve_worker(worker)

    rows = await TaskRun.filter(run_id__in=normalized).values_list("run_id", "worker_id")
    ownership = {str(run_id): worker_id for run_id, worker_id in rows}
    if set(ownership) != normalized:
        raise PermissionError("TaskRun 不存在或未分配给当前 Worker")
    if any(worker_id != resolved.id for worker_id in ownership.values()):
        raise PermissionError("TaskRun 不属于当前 Worker")


async def require_worker_owns_run(worker: Worker | str, run_id: str) -> None:
    await require_worker_owns_runs(worker, [run_id])


async def require_or_bind_worker_run_lease(
    worker: Worker | str,
    run_id: str,
    *,
    lease_id: str,
) -> None:
    """Bind an unclaimed TaskRun to this lease, or require the same lease."""
    normalized_run = run_id.strip()
    normalized_lease = lease_id.strip()
    if not normalized_run or normalized_run != run_id:
        raise ValueError("run_id 不合法")
    if not normalized_lease or normalized_lease != lease_id or len(normalized_lease) > MAX_LEASE_ID_LENGTH:
        raise ValueError("lease_id 不合法")
    resolved = await _resolve_worker(worker)
    execution = await TaskRun.filter(run_id=normalized_run).first()
    if execution is None or execution.worker_id != resolved.id:
        raise PermissionError("TaskRun 不存在或不属于当前 Worker")
    if execution.lease_id == normalized_lease:
        return
    if execution.lease_id is not None:
        raise PermissionError("TaskRun lease_id 与当前 Worker 代际不匹配")
    updated = await TaskRun.filter(
        id=execution.id,
        worker_id=resolved.id,
        lease_id__isnull=True,
    ).update(lease_id=normalized_lease)
    if updated == 1:
        return
    matching = await TaskRun.filter(
        id=execution.id,
        worker_id=resolved.id,
        lease_id=normalized_lease,
    ).exists()
    if not matching:
        raise PermissionError("TaskRun Lease 绑定发生并发冲突")


async def bind_worker_run_lease_generation(
    worker: Worker | str,
    run_id: str,
    *,
    lease_id: str,
    lease_gen: int,
    log_cutoff_id: str,
) -> None:
    """Persist an ownership-fenced generation and close its predecessor."""
    normalized_run = run_id.strip()
    normalized_lease = lease_id.strip()
    if not normalized_run or normalized_run != run_id:
        raise ValueError("run_id 不合法")
    if not normalized_lease or normalized_lease != lease_id or len(normalized_lease) > MAX_LEASE_ID_LENGTH:
        raise ValueError("lease_id 不合法")
    if isinstance(lease_gen, bool) or not isinstance(lease_gen, int) or lease_gen < 1:
        raise ValueError("lease_gen 必须是正整数")
    parse_stream_id(log_cutoff_id)

    resolved = await _resolve_worker(worker)
    async with in_transaction("default") as connection:
        execution = await _lock_claimable_run(normalized_run, resolved.id, connection)
        _require_monotonic_generation(execution, normalized_lease, lease_gen)
        await _record_generation_transition(
            execution=execution,
            worker_id=resolved.id,
            lease_id=normalized_lease,
            lease_gen=lease_gen,
            log_cutoff_id=log_cutoff_id,
            connection=connection,
        )
        await (
            TaskRun.filter(id=execution.id)
            .using_db(connection)
            .update(
                lease_id=normalized_lease,
                lease_gen=lease_gen,
            )
        )


async def _lock_claimable_run(run_id: str, worker_id: int, connection) -> TaskRun:
    execution = (
        await TaskRun.filter(run_id=run_id, worker_id=worker_id).using_db(connection).select_for_update().first()
    )
    if execution is None:
        raise PermissionError("TaskRun 不存在或不属于当前 Worker")
    if execution.status in _TASK_TERMINAL_STATUSES:
        raise PermissionError(f"TaskRun 已在终态 {execution.status}, 拒绝 bind")
    if execution.status not in _TASK_CLAIMABLE_STATUSES:
        raise PermissionError(f"TaskRun 状态 {execution.status} 不允许 bind")
    return execution


def _require_monotonic_generation(execution: TaskRun, lease_id: str, lease_gen: int) -> None:
    current_gen = execution.lease_gen
    if current_gen is None:
        return
    if execution.lease_id == lease_id:
        if lease_gen == current_gen:
            return
        raise PermissionError(f"TaskRun lease_id 不允许复用为不同代际: run_id={execution.run_id}")
    if lease_gen <= current_gen:
        raise PermissionError(f"TaskRun lease_gen 单调 CAS 失败: run_id={execution.run_id}")


async def _record_generation_transition(
    *,
    execution: TaskRun,
    worker_id: int,
    lease_id: str,
    lease_gen: int,
    log_cutoff_id: str,
    connection,
) -> None:
    if execution.lease_id and execution.lease_id != lease_id:
        await TaskRunLeaseGeneration.update_or_create(
            run_id=execution.run_id,
            lease_id=execution.lease_id,
            using_db=connection,
            defaults={
                "worker_id": worker_id,
                "lease_gen": execution.lease_gen,
                "log_valid_through_id": log_cutoff_id,
                "closed_at": datetime.now(UTC),
            },
        )
    await TaskRunLeaseGeneration.update_or_create(
        run_id=execution.run_id,
        lease_id=lease_id,
        using_db=connection,
        defaults={
            "worker_id": worker_id,
            "lease_gen": lease_gen,
            "log_valid_through_id": None,
            "closed_at": None,
        },
    )


async def require_worker_owns_runs_for_lease(
    worker: Worker | str,
    run_ids: Iterable[str],
    *,
    lease_id: str,
) -> None:
    """Require every run to belong to both the Worker and its lease generation."""
    normalized = {run_id.strip() for run_id in run_ids if run_id and run_id.strip()}
    normalized_lease = lease_id.strip()
    if not normalized:
        raise ValueError("run_id 不能为空")
    if not normalized_lease or normalized_lease != lease_id or len(normalized_lease) > MAX_LEASE_ID_LENGTH:
        raise ValueError("lease_id 不合法")

    resolved = await _resolve_worker(worker)
    rows = await TaskRun.filter(run_id__in=normalized).values_list(
        "run_id",
        "worker_id",
        "lease_id",
    )
    ownership = {str(run_id): (worker_id, stored_lease) for run_id, worker_id, stored_lease in rows}
    if set(ownership) != normalized:
        raise PermissionError("TaskRun 不存在或未分配给当前 Worker")
    if any(worker_id != resolved.id for worker_id, _lease in ownership.values()):
        raise PermissionError("TaskRun 不属于当前 Worker")
    if any(stored_lease != normalized_lease for _worker_id, stored_lease in ownership.values()):
        raise PermissionError("TaskRun lease_id 与当前 Worker 代际不匹配")


async def require_worker_owns_spider_run(
    worker: Worker | str,
    run_id: str,
    project_id: str,
    *,
    lease_id: str,
) -> None:
    normalized_run_id = run_id.strip()
    normalized_project_id = project_id.strip()
    normalized_lease_id = lease_id.strip()
    if not normalized_run_id or not normalized_project_id or not normalized_lease_id:
        raise ValueError("run_id、project_id 和 lease_id 不能为空")
    resolved = await _resolve_worker(worker)
    execution = await TaskRun.filter(run_id=normalized_run_id).first()
    if execution is None or execution.worker_id != resolved.id:
        raise PermissionError("TaskRun 不存在或不属于当前 Worker")
    if execution.status not in SPIDER_WRITABLE_TASK_STATUSES:
        raise PermissionError("TaskRun 已进入终态，拒绝继续写入 SpiderData")
    stored_lease_id = execution.lease_id
    if stored_lease_id and stored_lease_id != normalized_lease_id:
        raise StaleSpiderLeaseError("SpiderData lease_id 与 TaskRun 代际不匹配")
    await verify_spider_project_binding(execution, normalized_project_id)
    if stored_lease_id is None:
        updated = await TaskRun.filter(
            id=execution.id,
            worker_id=resolved.id,
            lease_id__isnull=True,
            status__in=SPIDER_WRITABLE_TASK_STATUSES,
        ).update(lease_id=normalized_lease_id)
        if updated != 1:
            raise PermissionError("TaskRun Lease 绑定发生并发冲突")


__all__ = [
    "bind_worker_run_lease_generation",
    "require_or_bind_worker_run_lease",
    "require_worker_owns_run",
    "require_worker_owns_runs",
    "require_worker_owns_runs_for_lease",
    "require_worker_owns_spider_run",
]
