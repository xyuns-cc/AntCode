"""Worker 到 TaskRun 的可信归属校验。"""

from __future__ import annotations

from collections.abc import Iterable

from antcode_core.application.services.crawl.spider_storage_cleanup import (
    SPIDER_WRITABLE_TASK_STATUSES,
)
from antcode_core.domain.models import Project, Task, TaskRun, Worker
from antcode_core.domain.models.enums import TaskStatus

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
    lease_gen: int | None = None,
) -> None:
    """fence ACQUIRED 之后把 run 绑定到当前代际（允许同 worker 换代改绑）。

    复审 P1-GW-02: 绑定权威是 run ownership fence Lua（已原子证明
    lease_id 是该 worker 的现行代际且 ownership 归其所有），本函数只负责
    把结论落到 PG。因此允许 X→Y 的同 worker 换代改绑——否则切代后 PG
    永远停在旧代际，新代际全部被拒。跨 worker 仍不可改绑（CAS 带
    worker_id）。必须在 fence 返回 ACQUIRED 后调用，禁止提前绑定。

    P1-GW-04: 当 lease_gen 传入时(推荐用 fence 时点的 Unix ms),用它做
    单调 CAS 谓词 (lease_gen IS NULL OR lease_gen <= NEW.lease_gen),防
    L1 fence ACQUIRED 后暂停 → L2 fence+bind → L1 迟到 bind 把 PG 从 L2
    覆盖回 L1 的竞态。lease_gen 为 None 时退回原行为(存量兼容);滚动升级
    完成后 Gateway 应始终传入非 None gen。
    """
    normalized_run = run_id.strip()
    normalized_lease = lease_id.strip()
    if not normalized_run or normalized_run != run_id:
        raise ValueError("run_id 不合法")
    if not normalized_lease or normalized_lease != lease_id or len(normalized_lease) > MAX_LEASE_ID_LENGTH:
        raise ValueError("lease_id 不合法")
    if lease_gen is not None and lease_gen < 0:
        raise ValueError("lease_gen 必须非负")

    resolved = await _resolve_worker(worker)

    # P1-GW-02 (round6): bind 前查 TaskRun 终态,已终态则拒绝 bind。防"L1 完成
    # 后 ACK 丢失, L2 reclaim + claim + bind → 重复执行副作用"。fence Lua 只
    # 保证 Redis ownership 单进程,不查 PG 终态; 这里补上 PG 侧的兜底。
    # (fence + bind 已经原子, 但终态判断放 PG 侧因为 Redis ownership TTL 短,
    #  终态可能在 Redis owner 已 DEL 后到, PG 是权威)
    existing = await TaskRun.filter(run_id=normalized_run).only("id", "status", "worker_id").first()
    if existing is not None and existing.status in _TASK_TERMINAL_STATUSES:
        raise PermissionError(
            f"TaskRun 已在终态 {existing.status}, 拒绝 bind (防已完成 run 被重复 claim 执行)"
        )

    if lease_gen is None:
        # 兼容路径:不做代际单调 CAS(仅当 Gateway 未升级时使用)
        updated = await TaskRun.filter(run_id=normalized_run, worker_id=resolved.id).update(
            lease_id=normalized_lease,
        )
        if updated == 0:
            raise PermissionError("TaskRun 不存在或不属于当前 Worker")
        return

    # P1-GW-04: 单调 CAS。Tortoise ORM 的 Q 支持
    #   Q(lease_gen__isnull=True) | Q(lease_gen__lte=lease_gen)
    from tortoise.expressions import Q

    updated = await TaskRun.filter(
        Q(lease_gen__isnull=True) | Q(lease_gen__lte=lease_gen),
        run_id=normalized_run,
        worker_id=resolved.id,
    ).update(lease_id=normalized_lease, lease_gen=lease_gen)
    if updated == 0:
        # 分辨"run 不存在" vs "gen CAS 失败"
        exists = await TaskRun.filter(run_id=normalized_run, worker_id=resolved.id).exists()
        if not exists:
            raise PermissionError("TaskRun 不存在或不属于当前 Worker")
        raise PermissionError(
            f"TaskRun lease_gen 单调 CAS 失败(有更新代际 bind 在先),拒绝旧代际覆盖: run_id={normalized_run}"
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
        raise PermissionError("SpiderData lease_id 与 TaskRun 代际不匹配")
    task = await Task.filter(id=execution.task_id).first()
    if task is None:
        raise PermissionError("TaskRun 关联任务不存在")
    if not await Project.filter(id=task.project_id, public_id=normalized_project_id).exists():
        raise PermissionError("SpiderData project_id 与 TaskRun 不匹配")
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
