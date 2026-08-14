"""Final Worker-use authorization check immediately before dispatch binding."""

from __future__ import annotations

from typing import cast

from antcode_core.domain.models import Task, TaskRun, User, UserWorkerPermission
from antcode_core.domain.models.enums import WorkerPermission


class DispatchWorkerAccessDenied(RuntimeError):
    """A Task owner cannot use the selected Worker."""


async def require_task_run_worker_use_access(tasks: list[dict], worker_id: int) -> None:
    run_ids = {str(task.get("run_id") or "") for task in tasks}
    if "" in run_ids:
        raise DispatchWorkerAccessDenied("派发任务缺少耐久 run_id，无法复验 Worker 权限")
    owners = await _load_task_run_owners(run_ids)
    admin_ids, permitted_ids = await _load_worker_access(set(owners.values()), worker_id)
    unauthorized = sorted(set(owners.values()) - admin_ids - permitted_ids)
    if unauthorized:
        raise DispatchWorkerAccessDenied(f"任务所有者没有目标 Worker 的 use 权限: user_ids={unauthorized}")


async def _load_task_run_owners(run_ids: set[str]) -> dict[str, int]:
    runs = await TaskRun.filter(run_id__in=run_ids).only("run_id", "task_id").all()
    found_run_ids = {str(run.run_id) for run in runs}
    missing_runs = sorted(run_ids - found_run_ids)
    if missing_runs:
        raise DispatchWorkerAccessDenied(f"派发任务缺少 TaskRun 归属记录: run_ids={missing_runs}")
    tasks = await Task.filter(id__in={run.task_id for run in runs}).only("id", "user_id").all()
    owner_by_task = {task.id: task.user_id for task in tasks}
    missing_tasks = sorted({run.task_id for run in runs} - owner_by_task.keys())
    if missing_tasks:
        raise DispatchWorkerAccessDenied(f"派发任务缺少所有者: task_ids={missing_tasks}")
    return {str(run.run_id): owner_by_task[run.task_id] for run in runs}


async def _load_worker_access(owner_ids: set[int], worker_id: int) -> tuple[set[int], set[int]]:
    admins = await User.filter(id__in=owner_ids, is_admin=True).values_list("id", flat=True)
    permissions = await UserWorkerPermission.filter(
        user_id__in=owner_ids,
        worker_id=worker_id,
        permission=WorkerPermission.USE.value,
    ).values_list("user_id", flat=True)
    return set(cast(list[int], admins)), set(cast(list[int], permissions))


__all__ = ["DispatchWorkerAccessDenied", "require_task_run_worker_use_access"]
