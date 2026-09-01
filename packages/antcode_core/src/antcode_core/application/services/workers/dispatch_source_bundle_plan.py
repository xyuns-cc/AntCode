"""算出一批任务需要哪些项目的 source bundle，并把下载信息取回来。

从 ``worker_dispatcher`` 拆出来是因为它回答的是一个与"派给谁"无关的问题：**这批任务
要跑起来，得先把哪些项目的代码送过去**。rule 任务的定义随参数下发，压根不需要 bundle；
其余项目按 run 分组构建。它的失效模式也自成一类——项目同步/打包失败，既不是容量不足，
也不是队列写不进去，而是"代码没准备好"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RULE_PROJECT_TYPE = "rule"
_DEFAULT_SYNC_FAILURE_REASON = "项目同步失败"


@dataclass(frozen=True)
class SourceBundlePlan:
    """一批任务的 bundle 准备结果。``failure_reason`` 非空即代表这批不能继续派发。"""

    sync_results: dict[str, Any]
    run_download_info: dict[str, Any]
    failure_reason: str | None = None


def _empty_sync_results() -> dict[str, Any]:
    return {"synced": [], "skipped": [], "failed": []}


def group_run_ids_by_project(tasks: list[dict]) -> dict[str, list[str]]:
    """rule 任务不参与 bundle，因此也不进这张表。"""
    grouped: dict[str, list[str]] = {}
    for task in tasks:
        if task.get("project_type") == RULE_PROJECT_TYPE:
            continue
        project_id = task.get("project_id")
        run_id = task.get("run_id") or task.get("task_id")
        if not project_id or not run_id:
            continue
        project_run_ids = grouped.setdefault(project_id, [])
        if run_id not in project_run_ids:
            project_run_ids.append(run_id)
    return grouped


def bundle_project_ids(tasks: list[dict]) -> list[str]:
    """需要 bundle 的项目，保持首次出现的顺序。"""
    project_ids: list[str] = []
    for task in tasks:
        project_id = task.get("project_id")
        if not project_id or task.get("project_type") == RULE_PROJECT_TYPE:
            continue
        if project_id not in project_ids:
            project_ids.append(project_id)
    return project_ids


async def prepare_source_bundles(worker: Any, tasks: list[dict]) -> SourceBundlePlan:
    """Master 侧构建 source bundle；全是 rule 任务时直接返回空计划。"""
    from antcode_core.application.services.workers.source_bundle_dispatch_service import (
        source_bundle_dispatch_service,
    )

    project_ids = bundle_project_ids(tasks)
    if not project_ids:
        return SourceBundlePlan(sync_results=_empty_sync_results(), run_download_info={})

    sync_results, run_download_info = await source_bundle_dispatch_service.build_dispatch_for_worker_with_info(
        worker,
        project_ids,
        run_ids_by_project=group_run_ids_by_project(tasks),
    )
    failed_items = sync_results.get("failed") or []
    if not failed_items:
        return SourceBundlePlan(sync_results=sync_results, run_download_info=run_download_info)
    return SourceBundlePlan(
        sync_results=sync_results,
        run_download_info=run_download_info,
        failure_reason=failed_items[0].get("reason") or _DEFAULT_SYNC_FAILURE_REASON,
    )


__all__ = [
    "SourceBundlePlan",
    "bundle_project_ids",
    "group_run_ids_by_project",
    "prepare_source_bundles",
]
