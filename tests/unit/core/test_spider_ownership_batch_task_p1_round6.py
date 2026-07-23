"""P1-round6 5.2 回归:batch-issued TaskRun (task_id=0) SpiderData ownership。

审查文档 round6 5.2:
`Gateway batch 使用 task_id=0, SpiderData ownership 强制查真实 Task, 批次
数据上报合同冲突`。

Bug 场景:
- batch_dispatcher 建 TaskRun 占位, task_id=0 (batch 一期不挂 Task)
- Worker 执行 batch item 上报 SpiderData
- Gateway → require_worker_owns_spider_run(worker, run_id, project_id)
- 原实现 `Task.filter(id=execution.task_id=0)` 查不到 → PermissionError
- 所有 batch SpiderData 上报被拒

修复:task_id=0 时跳过 Task 反查, 直接按 project_id 存在性校验 Project。
task_id>0 时保持原 Task → project_id 反向校验路径。

本测试锁死:
1. task_id=0 + project_id 存在 → pass
2. task_id=0 + project_id 不存在 → PermissionError
3. task_id>0 保持原契约不变 (依然要求 Task 存在且 project 匹配)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.workers import run_ownership_service


class _FakeQS:
    def __init__(self, first_result=None, exists_result=False, update_result=0):
        self._first = first_result
        self._exists = exists_result
        self._update = update_result

    async def first(self):
        return self._first

    async def exists(self):
        return self._exists

    async def update(self, **_kwargs):
        return self._update


@pytest.mark.asyncio
async def test_batch_task_id_zero_allows_by_project_existence(monkeypatch):
    """P1-round6 5.2: task_id=0 + project 存在 → pass。"""
    worker = MagicMock(id=42)
    execution = MagicMock(id=1, worker_id=42, lease_id="L1", status="RUNNING", task_id=0)

    monkeypatch.setattr(run_ownership_service, "SPIDER_WRITABLE_TASK_STATUSES", {"RUNNING"})
    monkeypatch.setattr(run_ownership_service, "_resolve_worker", AsyncMock(return_value=worker))

    with (
        patch.object(run_ownership_service, "TaskRun") as MockTR,
        patch.object(run_ownership_service, "Project") as MockProject,
        patch.object(run_ownership_service, "Task") as MockTask,
    ):
        MockTR.filter = MagicMock(return_value=_FakeQS(first_result=execution))
        MockProject.filter = MagicMock(return_value=_FakeQS(exists_result=True))

        await run_ownership_service.require_worker_owns_spider_run(worker, "run-1", "proj-a", lease_id="L1")
        # 关键: Task.filter 从未被调用 (task_id=0 分支)
        MockTask.filter.assert_not_called()
        MockProject.filter.assert_called_once_with(public_id="proj-a")


@pytest.mark.asyncio
async def test_batch_task_id_zero_rejects_when_project_missing(monkeypatch):
    """P1-round6 5.2: task_id=0 + project 不存在 → PermissionError。"""
    worker = MagicMock(id=42)
    execution = MagicMock(id=1, worker_id=42, lease_id="L1", status="RUNNING", task_id=0)

    monkeypatch.setattr(run_ownership_service, "SPIDER_WRITABLE_TASK_STATUSES", {"RUNNING"})
    monkeypatch.setattr(run_ownership_service, "_resolve_worker", AsyncMock(return_value=worker))

    with (
        patch.object(run_ownership_service, "TaskRun") as MockTR,
        patch.object(run_ownership_service, "Project") as MockProject,
    ):
        MockTR.filter = MagicMock(return_value=_FakeQS(first_result=execution))
        MockProject.filter = MagicMock(return_value=_FakeQS(exists_result=False))

        with pytest.raises(PermissionError, match="project_id 不存在"):
            await run_ownership_service.require_worker_owns_spider_run(worker, "run-1", "proj-missing", lease_id="L1")


@pytest.mark.asyncio
async def test_regular_task_id_still_requires_task(monkeypatch):
    """P1-round6 5.2 反面: task_id>0 保持原 Task 反查契约不变。"""
    worker = MagicMock(id=42)
    execution = MagicMock(id=1, worker_id=42, lease_id="L1", status="RUNNING", task_id=99)

    monkeypatch.setattr(run_ownership_service, "SPIDER_WRITABLE_TASK_STATUSES", {"RUNNING"})
    monkeypatch.setattr(run_ownership_service, "_resolve_worker", AsyncMock(return_value=worker))

    with (
        patch.object(run_ownership_service, "TaskRun") as MockTR,
        patch.object(run_ownership_service, "Task") as MockTask,
        patch.object(run_ownership_service, "Project") as MockProject,
    ):
        MockTR.filter = MagicMock(return_value=_FakeQS(first_result=execution))
        # Task 不存在 → 拒
        MockTask.filter = MagicMock(return_value=_FakeQS(first_result=None))

        with pytest.raises(PermissionError, match="关联任务不存在"):
            await run_ownership_service.require_worker_owns_spider_run(worker, "run-1", "proj-a", lease_id="L1")
        MockTask.filter.assert_called_once_with(id=99)
        MockProject.filter.assert_not_called()
