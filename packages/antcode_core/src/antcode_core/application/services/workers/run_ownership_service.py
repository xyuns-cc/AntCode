"""Worker 到 TaskRun 的可信归属校验。"""

from __future__ import annotations

from collections.abc import Iterable

from antcode_core.domain.models import TaskRun, Worker


async def require_worker_owns_runs(
    worker: Worker | str,
    run_ids: Iterable[str],
) -> None:
    normalized = {run_id.strip() for run_id in run_ids if run_id and run_id.strip()}
    if not normalized:
        raise ValueError("run_id 不能为空")

    resolved = await Worker.get_or_none(public_id=worker) if isinstance(worker, str) else worker
    if resolved is None:
        raise PermissionError("Worker 不存在")

    rows = await TaskRun.filter(run_id__in=normalized).values_list("run_id", "worker_id")
    ownership = {str(run_id): worker_id for run_id, worker_id in rows}
    if set(ownership) != normalized:
        raise PermissionError("TaskRun 不存在或未分配给当前 Worker")
    if any(worker_id != resolved.id for worker_id in ownership.values()):
        raise PermissionError("TaskRun 不属于当前 Worker")


async def require_worker_owns_run(worker: Worker | str, run_id: str) -> None:
    await require_worker_owns_runs(worker, [run_id])


__all__ = ["require_worker_owns_run", "require_worker_owns_runs"]
