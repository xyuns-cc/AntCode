"""容量类失败必须自带结构化码，且与"我们坏了"那类分得开。

从前 ``dispatch_batch`` 的失败只有一个 ``error`` 字段，路由据此一律回 500——"现在没有
空闲 Worker"（等一会儿再来）和"任务写不进 Redis"（我们坏了）长得一模一样。这组用例钉住
的是**产出侧**：每条失败路径打的是哪个码。路由侧的状态码映射见
``tests/unit/web_api/test_dispatch_failure_status_codes.py``。

证伪方式：把 ``admit_dispatch_worker`` 的两个容量码换成 ``DISPATCH_UNEXPECTED_ERROR``，
或把 ``_publish_batch`` 的写失败码换成容量码，对应用例立刻变红。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.workers import worker_dispatcher as dispatcher_module
from antcode_core.application.services.workers import worker_load_balancing, worker_selection
from antcode_core.application.services.workers.dispatch_error_codes import (
    CAPACITY_ERROR_CODES,
    DISPATCH_EMPTY_BATCH,
    DISPATCH_NO_CAPACITY,
    DISPATCH_QUEUE_WRITE_FAILED,
    DISPATCH_WORKER_OFFLINE,
)
from antcode_core.application.services.workers.worker_dispatch_admission import DispatchAdmission
from antcode_core.application.services.workers.worker_dispatcher import BatchDispatchResult, WorkerTaskDispatcher
from antcode_core.domain.models.enums import WorkerStatus
from antcode_core.domain.models.worker import Worker

_TASKS = [{"task_id": "run-1", "project_id": "p-1", "project_type": "rule"}]
_WORKER_ROW_ID = 7


def _worker(*, last_heartbeat):
    return SimpleNamespace(
        id=_WORKER_ROW_ID,
        public_id="worker-7",
        name="Worker 7",
        region=None,
        status=WorkerStatus.ONLINE,
        capabilities={"task_types": ["rule"]},
        last_heartbeat=last_heartbeat,
    )


@pytest.mark.asyncio
async def test_empty_batch_is_tagged_and_is_not_a_capacity_failure() -> None:
    result = await WorkerTaskDispatcher().dispatch_batch(tasks=[])

    assert result.error_code == DISPATCH_EMPTY_BATCH
    assert result.error_code not in CAPACITY_ERROR_CODES


@pytest.mark.asyncio
async def test_no_eligible_worker_is_a_capacity_failure(monkeypatch) -> None:
    monkeypatch.setattr(Worker, "filter", lambda **_filters: SimpleNamespace(all=AsyncMock(return_value=[])))
    monkeypatch.setattr(worker_load_balancing, "filter_registration_ready_workers", AsyncMock(return_value=[]))

    result = await WorkerTaskDispatcher().dispatch_batch(tasks=_TASKS)

    assert result.success is False
    assert result.error_code == DISPATCH_NO_CAPACITY
    assert result.error_code in CAPACITY_ERROR_CODES


@pytest.mark.asyncio
async def test_stale_heartbeat_is_a_capacity_failure(monkeypatch) -> None:
    worker = _worker(last_heartbeat=None)
    monkeypatch.setattr(Worker, "filter", lambda **_filters: SimpleNamespace(first=AsyncMock(return_value=worker)))
    monkeypatch.setattr(worker_selection, "has_unacknowledged_v2_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(
        worker_selection,
        "resolve_capability_map",
        AsyncMock(return_value={_WORKER_ROW_ID: {"task_types": ["rule"]}}),
    )

    result = await WorkerTaskDispatcher().dispatch_batch(tasks=_TASKS, worker_id="worker-7")

    assert result.error == "Worker 未在线: Worker 7"
    assert result.error_code == DISPATCH_WORKER_OFFLINE
    assert result.error_code in CAPACITY_ERROR_CODES


@pytest.mark.asyncio
async def test_queue_write_failure_is_not_a_capacity_failure(monkeypatch) -> None:
    """Redis 写不进去是"我们坏了"，绝不能被归进容量类而让调用方以为重试就好。"""
    worker = _worker(last_heartbeat=None)
    dispatcher = WorkerTaskDispatcher()
    dispatcher._bind_task_runs_to_worker = AsyncMock(return_value=1)
    monkeypatch.setattr(
        dispatcher_module,
        "admit_dispatch_worker",
        AsyncMock(return_value=DispatchAdmission(worker=worker)),
    )
    monkeypatch.setattr(
        dispatcher_module,
        "require_worker_current_requirements",
        AsyncMock(return_value=LeaseCapabilitySnapshot("lease-7", '{"task_types":["rule"]}', 7)),
    )
    monkeypatch.setattr(
        dispatcher_module,
        "publish_ready_batch_to_worker",
        AsyncMock(return_value={"success": False, "error": "connection reset by peer"}),
    )

    result = await dispatcher.dispatch_batch(tasks=_TASKS)

    assert result.success is False
    assert result.error_code == DISPATCH_QUEUE_WRITE_FAILED
    assert result.error_code not in CAPACITY_ERROR_CODES


def test_a_codeless_failure_cannot_be_constructed() -> None:
    """不变量本身：漏打码的失败在构造期就炸，不会一路飘到 HTTP 边界变成 500。"""
    with pytest.raises(ValueError, match="error_code"):
        BatchDispatchResult(success=False, error="忘了打码")
