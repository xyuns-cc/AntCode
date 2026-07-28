"""SpiderData run-to-project authorization helpers."""

from __future__ import annotations

from typing import Any

from antcode_core.domain.models import CrawlBatch, Project, Task


class StaleSpiderLeaseError(PermissionError):
    """The run is already bound to another Worker lease generation."""


async def verify_spider_project_binding(execution: Any, project_public_id: str) -> None:
    """Require a regular or batch-issued run to belong to the supplied project."""
    if execution.task_id and int(execution.task_id) > 0:
        await _verify_task_project(execution.task_id, project_public_id)
        return
    await _verify_batch_project(execution.result_data, project_public_id)


async def _verify_task_project(task_id: int, project_public_id: str) -> None:
    task = await Task.filter(id=task_id).first()
    if task is None:
        raise PermissionError("TaskRun 关联任务不存在")
    matches = await Project.filter(id=task.project_id, public_id=project_public_id).exists()
    if not matches:
        raise PermissionError("SpiderData project_id 与 TaskRun 不匹配")


async def _verify_batch_project(result_data: Any, project_public_id: str) -> None:
    batch_id = result_data.get("crawl_batch_id") if isinstance(result_data, dict) else None
    if not isinstance(batch_id, str) or not batch_id.strip() or batch_id != batch_id.strip():
        raise PermissionError("批次 TaskRun 缺少合法 crawl_batch_id")
    batch = await CrawlBatch.filter(public_id=batch_id).only("project_id").first()
    if batch is None:
        raise PermissionError("TaskRun 关联爬取批次不存在")
    matches = await Project.filter(id=batch.project_id, public_id=project_public_id).exists()
    if not matches:
        raise PermissionError("SpiderData project_id 与爬取批次不匹配")


__all__ = ["StaleSpiderLeaseError", "verify_spider_project_binding"]
