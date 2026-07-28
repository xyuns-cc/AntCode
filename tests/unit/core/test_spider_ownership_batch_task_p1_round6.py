"""Batch-issued TaskRun SpiderData project binding regression tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.workers import run_ownership_service, spider_run_access


class _FakeQS:
    def __init__(self, first_result=None, exists_result=False, update_result=0):
        self._first = first_result
        self._exists = exists_result
        self._update = update_result

    async def first(self):
        return self._first

    def only(self, *_fields):
        return self

    async def exists(self):
        return self._exists

    async def update(self, **_kwargs):
        return self._update


@pytest.mark.asyncio
async def test_batch_task_id_zero_requires_its_real_project(monkeypatch):
    worker = MagicMock(id=42)
    execution = MagicMock(
        id=1,
        worker_id=42,
        lease_id="L1",
        status="RUNNING",
        task_id=0,
        result_data={"crawl_batch_id": "batch-1"},
    )
    batch = MagicMock(project_id=7)

    monkeypatch.setattr(run_ownership_service, "SPIDER_WRITABLE_TASK_STATUSES", {"RUNNING"})
    monkeypatch.setattr(run_ownership_service, "_resolve_worker", AsyncMock(return_value=worker))

    with (
        patch.object(run_ownership_service, "TaskRun") as MockTR,
        patch.object(spider_run_access, "CrawlBatch") as MockBatch,
        patch.object(spider_run_access, "Project") as MockProject,
    ):
        MockTR.filter = MagicMock(return_value=_FakeQS(first_result=execution))
        MockBatch.filter = MagicMock(return_value=_FakeQS(first_result=batch))
        MockProject.filter = MagicMock(return_value=_FakeQS(exists_result=True))

        await run_ownership_service.require_worker_owns_spider_run(worker, "run-1", "proj-a", lease_id="L1")
        MockBatch.filter.assert_called_once_with(public_id="batch-1")
        MockProject.filter.assert_called_once_with(id=7, public_id="proj-a")


@pytest.mark.asyncio
async def test_batch_task_id_zero_rejects_foreign_existing_project(monkeypatch):
    worker = MagicMock(id=42)
    execution = MagicMock(
        id=1,
        worker_id=42,
        lease_id="L1",
        status="RUNNING",
        task_id=0,
        result_data={"crawl_batch_id": "batch-1"},
    )
    batch = MagicMock(project_id=7)

    monkeypatch.setattr(run_ownership_service, "SPIDER_WRITABLE_TASK_STATUSES", {"RUNNING"})
    monkeypatch.setattr(run_ownership_service, "_resolve_worker", AsyncMock(return_value=worker))

    with (
        patch.object(run_ownership_service, "TaskRun") as MockTR,
        patch.object(spider_run_access, "CrawlBatch") as MockBatch,
        patch.object(spider_run_access, "Project") as MockProject,
    ):
        MockTR.filter = MagicMock(return_value=_FakeQS(first_result=execution))
        MockBatch.filter = MagicMock(return_value=_FakeQS(first_result=batch))
        MockProject.filter = MagicMock(return_value=_FakeQS(exists_result=False))

        with pytest.raises(PermissionError, match="爬取批次不匹配"):
            await run_ownership_service.require_worker_owns_spider_run(worker, "run-1", "proj-foreign", lease_id="L1")


@pytest.mark.asyncio
async def test_regular_task_id_still_requires_task(monkeypatch):
    """P1-round6 5.2 反面: task_id>0 保持原 Task 反查契约不变。"""
    worker = MagicMock(id=42)
    execution = MagicMock(id=1, worker_id=42, lease_id="L1", status="RUNNING", task_id=99, result_data=None)

    monkeypatch.setattr(run_ownership_service, "SPIDER_WRITABLE_TASK_STATUSES", {"RUNNING"})
    monkeypatch.setattr(run_ownership_service, "_resolve_worker", AsyncMock(return_value=worker))

    with (
        patch.object(run_ownership_service, "TaskRun") as MockTR,
        patch.object(spider_run_access, "Task") as MockTask,
        patch.object(spider_run_access, "Project") as MockProject,
    ):
        MockTR.filter = MagicMock(return_value=_FakeQS(first_result=execution))
        # Task 不存在 → 拒
        MockTask.filter = MagicMock(return_value=_FakeQS(first_result=None))

        with pytest.raises(PermissionError, match="关联任务不存在"):
            await run_ownership_service.require_worker_owns_spider_run(worker, "run-1", "proj-a", lease_id="L1")
        MockTask.filter.assert_called_once_with(id=99)
        MockProject.filter.assert_not_called()
