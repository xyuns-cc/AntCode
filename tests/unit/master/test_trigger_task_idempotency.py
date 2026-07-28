"""P1-DB-01: outbox ``task_trigger`` 重放不得重复触发任务。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_master.control.scheduler_loop import SchedulerService
from antcode_master.control.trigger_idempotency import TRIGGER_RUN_NAMESPACE


def _service() -> SchedulerService:
    service = SchedulerService.__new__(SchedulerService)
    # spec 掉 timezone 属性：datetime.now(tzinfo) 不接受 MagicMock。
    service.scheduler = MagicMock(spec=["get_job", "add_job"])
    return service


_RUNS_PATH = "antcode_master.control.trigger_idempotency.TaskRun"


@pytest.mark.asyncio
async def test_replay_with_existing_run_does_not_schedule_again():
    service = _service()
    exists = MagicMock(return_value=MagicMock(exists=AsyncMock(return_value=True)))

    with patch(f"{_RUNS_PATH}.filter", exists):
        await service.trigger_task(42, idempotency_key="outbox-1")

    service.scheduler.add_job.assert_not_called()
    expected_run_id = str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, "42:outbox-1"))
    assert exists.call_args.kwargs["run_id"] == expected_run_id


@pytest.mark.asyncio
async def test_first_delivery_schedules_deterministic_job_and_run_id():
    service = _service()
    exists = MagicMock(return_value=MagicMock(exists=AsyncMock(return_value=False)))

    with patch(f"{_RUNS_PATH}.filter", exists):
        await service.trigger_task(42, idempotency_key="outbox-1")

    kwargs = service.scheduler.add_job.call_args.kwargs
    # 确定性 job id：作业已挂、consumed 未标的窗口内重放被 replace_existing 折叠。
    assert kwargs["id"] == "42_outbox_outbox-1"
    assert kwargs["replace_existing"] is True
    expected_run_id = str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, "42:outbox-1"))
    assert kwargs["kwargs"] == {"task_id": 42, "fixed_run_id": expected_run_id}


@pytest.mark.asyncio
async def test_trigger_without_key_keeps_legacy_behavior():
    service = _service()
    service.scheduler.get_job = MagicMock(return_value=None)

    await service.trigger_task(42)

    kwargs = service.scheduler.add_job.call_args.kwargs
    assert kwargs["id"].startswith("42_manual_")
    assert kwargs["kwargs"] == {"task_id": 42}
