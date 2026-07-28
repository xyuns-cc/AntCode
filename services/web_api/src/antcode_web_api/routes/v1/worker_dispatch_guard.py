"""批量分发的授权 + 可重派状态守卫（P1-FN-03）。

``/workers/dispatch/batch`` 端点在进入 dispatcher 之前完成：

1. 项目/任务/运行记录的归属校验（防 IDOR / 跨项目借用）；
2. P1-FN-03 可重派状态校验：终态/运行中/已取消的 run 逐条返回 409 冲突，
   拒绝整批重派 —— 重派会重复外部副作用，而新结果会被终态闸门拒绝。
   竞态窗口（校验通过后状态变化）由 worker_dispatcher 绑定侧的同一套
   CAS（``dispatch_bind_guard``）原子兜底。
"""

from typing import Any, NoReturn

from antcode_core.domain.models import DispatchStatus, Task, TaskRun, TaskStatus
from fastapi import HTTPException, status

# P1-FN-03: 可重派状态集合(与 dispatch_bind_guard 的 scoped 集合一致):
# dispatch 仍在 PENDING(首次派发)或已判 FAILED(派发层失败的显式重派),
# runtime 从未启动,且未被用户取消。
DISPATCHABLE_DISPATCH_STATUSES = (DispatchStatus.PENDING, DispatchStatus.FAILED)


def _batch_not_found(index: int, entity: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"tasks[{index}] {entity}不存在或不属于当前项目",
    )


async def _resolve_batch_projects(tasks, user_id: int, project_service) -> dict[str, Any]:
    projects: dict[str, Any] = {}
    for index, item in enumerate(tasks):
        if item.project_id in projects:
            continue
        project = await project_service.get_project_by_id(item.project_id, user_id)
        if project is None:
            _batch_not_found(index, "项目")
        projects[item.project_id] = project
    return projects


async def _load_authorized_batch_tasks(tasks, projects: dict[str, Any]) -> dict[int, Task]:
    task_ids = {item.task_id for item in tasks}
    rows = await Task.filter(id__in=task_ids).only("id", "project_id", "user_id").all()
    tasks_by_id = {row.id: row for row in rows}
    for index, item in enumerate(tasks):
        project = projects[item.project_id]
        task = tasks_by_id.get(item.task_id)
        if task is None or task.project_id != project.id or task.user_id != project.user_id:
            _batch_not_found(index, "任务")
    return tasks_by_id


async def _load_authorized_batch_runs(tasks, tasks_by_id: dict[int, Task]) -> None:
    run_ids = [item.run_id for item in tasks]
    if len(set(run_ids)) != len(run_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="批量任务的 run_id 不得重复")
    rows = (
        await TaskRun.filter(run_id__in=set(run_ids))
        .only("run_id", "task_id", "status", "dispatch_status", "runtime_status")
        .all()
    )
    runs_by_id = {row.run_id: row for row in rows}
    conflicts: list[dict] = []
    for index, item in enumerate(tasks):
        run = runs_by_id.get(item.run_id)
        task = tasks_by_id[item.task_id]
        if run is None or run.task_id != task.id:
            _batch_not_found(index, "运行记录")
        if not _is_run_dispatchable(run):
            conflicts.append(_run_conflict_detail(index, run))
    if conflicts:
        # P1-FN-03: 终态/运行中/已取消 run 逐条返回冲突,拒绝整批重派。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "存在不可重派的运行记录", "conflicts": conflicts},
        )


def _is_run_dispatchable(run: TaskRun) -> bool:
    return (
        run.dispatch_status in DISPATCHABLE_DISPATCH_STATUSES
        and run.runtime_status is None
        and run.status != TaskStatus.CANCELLED
    )


def _run_conflict_detail(index: int, run: TaskRun) -> dict:
    return {
        "index": index,
        "run_id": run.run_id,
        "status": getattr(run.status, "value", run.status),
        "dispatch_status": getattr(run.dispatch_status, "value", run.dispatch_status),
        "runtime_status": getattr(run.runtime_status, "value", run.runtime_status),
    }


async def authorize_batch_dispatch_tasks(tasks, user_id: int, project_service) -> list[dict]:
    projects = await _resolve_batch_projects(tasks, user_id, project_service)
    tasks_by_id = await _load_authorized_batch_tasks(tasks, projects)
    await _load_authorized_batch_runs(tasks, tasks_by_id)
    authorized: list[dict] = []
    for item in tasks:
        project = projects[item.project_id]
        payload = item.model_dump()
        payload["runtime_env_name"] = (
            project.worker_env_name if getattr(project, "env_location", None) == "worker" else ""
        ) or ""
        payload["_dispatch_scope"] = {
            "run_id": item.run_id,
            "task_id": item.task_id,
            "project_id": project.id,
            "owner_id": project.user_id,
        }
        authorized.append(payload)
    return authorized


__all__ = ["DISPATCHABLE_DISPATCH_STATUSES", "authorize_batch_dispatch_tasks"]
