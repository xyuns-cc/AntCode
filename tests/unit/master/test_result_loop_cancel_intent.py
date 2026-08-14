"""ResultLoop must treat a durable cancel request as retry-ineligible."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models import Task, TaskRun
from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus
from antcode_master.control import scheduler_loop
from antcode_master.ingester.result_loop import ResultLoop


def _only_first_query(row):
    return SimpleNamespace(only=MagicMock(return_value=SimpleNamespace(first=AsyncMock(return_value=row))))


@pytest.mark.asyncio
async def test_schedule_remote_retry_skips_cancel_requested_run(monkeypatch):
    execution = SimpleNamespace(
        id=1,
        run_id="run-cancel-requested",
        task_id=7,
        retry_count=0,
        result_data=None,
        status=TaskStatus.FAILED,
        runtime_status=RuntimeStatus.FAILED,
        cancel_requested_at=datetime.now(UTC),
    )
    task_filter = MagicMock()
    schedule_retry = AsyncMock()
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_only_first_query(execution)))
    monkeypatch.setattr(Task, "filter", task_filter)
    monkeypatch.setattr(scheduler_loop.scheduler_service, "_schedule_retry", schedule_retry)

    await ResultLoop()._schedule_remote_retry("run-cancel-requested")

    task_filter.assert_not_called()
    schedule_retry.assert_not_awaited()
