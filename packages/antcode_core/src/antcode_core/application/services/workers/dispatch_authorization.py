"""Final Worker-use authorization check immediately before dispatch binding."""

from __future__ import annotations

from typing import Any, cast

from antcode_core.domain.models import CrawlBatch, Task, TaskRun, User, UserWorkerPermission
from antcode_core.domain.models.enums import WorkerPermission
from antcode_core.domain.models.task_run import TASK_ID_ABSENT


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
    """解析每个 run 的所有者。

    run 分两类且都必须解析出真实所有者，任一解析不出即拒绝派发：
    - 计划任务 run：owner = ``Task.user_id``；
    - 爬取批次 run：没有 Task 行（``task_id == TASK_ID_ABSENT``），
      owner = ``CrawlBatch.user_id``，批次身份取自 ``result_data.crawl_batch_id``。
    """
    runs = await TaskRun.filter(run_id__in=run_ids).only("run_id", "task_id", "result_data").all()
    found_run_ids = {str(run.run_id) for run in runs}
    missing_runs = sorted(run_ids - found_run_ids)
    if missing_runs:
        raise DispatchWorkerAccessDenied(f"派发任务缺少 TaskRun 归属记录: run_ids={missing_runs}")
    batch_runs = [run for run in runs if int(run.task_id) == TASK_ID_ABSENT]
    task_runs = [run for run in runs if int(run.task_id) != TASK_ID_ABSENT]
    return {**await _task_run_owners(task_runs), **await _batch_run_owners(batch_runs)}


async def _task_run_owners(runs: list[Any]) -> dict[str, int]:
    if not runs:
        return {}
    tasks = await Task.filter(id__in={run.task_id for run in runs}).only("id", "user_id").all()
    owner_by_task = {task.id: task.user_id for task in tasks}
    missing_tasks = sorted({run.task_id for run in runs} - owner_by_task.keys())
    if missing_tasks:
        raise DispatchWorkerAccessDenied(f"派发任务缺少所有者: task_ids={missing_tasks}")
    return {str(run.run_id): owner_by_task[run.task_id] for run in runs}


async def _batch_run_owners(runs: list[Any]) -> dict[str, int]:
    if not runs:
        return {}
    batch_by_run = {str(run.run_id): _batch_id_of(run) for run in runs}
    batches = await CrawlBatch.filter(public_id__in=set(batch_by_run.values())).only("public_id", "user_id").all()
    owner_by_batch = {str(batch.public_id): batch.user_id for batch in batches}
    missing_batches = sorted(set(batch_by_run.values()) - owner_by_batch.keys())
    if missing_batches:
        raise DispatchWorkerAccessDenied(f"派发任务缺少所有者: crawl_batch_ids={missing_batches}")
    return {run_id: owner_by_batch[batch_id] for run_id, batch_id in batch_by_run.items()}


def _batch_id_of(run: Any) -> str:
    result_data = run.result_data
    batch_id = result_data.get("crawl_batch_id") if isinstance(result_data, dict) else None
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise DispatchWorkerAccessDenied(f"批次 TaskRun 缺少 crawl_batch_id，无法解析所有者: run_id={run.run_id}")
    return batch_id


async def _load_worker_access(owner_ids: set[int], worker_id: int) -> tuple[set[int], set[int]]:
    admins = await User.filter(id__in=owner_ids, is_admin=True).values_list("id", flat=True)
    permissions = await UserWorkerPermission.filter(
        user_id__in=owner_ids,
        worker_id=worker_id,
        permission=WorkerPermission.USE.value,
    ).values_list("user_id", flat=True)
    return set(cast(list[int], admins)), set(cast(list[int], permissions))


__all__ = ["DispatchWorkerAccessDenied", "require_task_run_worker_use_access"]
