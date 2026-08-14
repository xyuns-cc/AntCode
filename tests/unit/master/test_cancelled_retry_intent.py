from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_master.control import retry_intent_guard, scheduler_loop


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Query:
    def __init__(self, *, first=None):
        self.first = AsyncMock(return_value=first)
        self.update = AsyncMock(return_value=1)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


@pytest.mark.asyncio
async def test_cancel_requested_run_never_claims_retry_intent(monkeypatch) -> None:
    source = SimpleNamespace(
        id=10,
        run_id="run-cancel-requested",
        retry_count=0,
        result_data={},
        next_retry_at=None,
        cancel_requested_at=datetime.now(UTC),
    )
    query = _Query(first=source)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=query))
    task = SimpleNamespace(id=1, retry_count=1, retry_delay=30)
    monkeypatch.setattr(scheduler_loop.Task, "filter", MagicMock(return_value=_Query(first=task)))

    intent = await scheduler_loop.SchedulerService()._claim_retry_intent(task, source)

    assert intent is None
    query.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_retry_source_rejects_persisted_cancel_request(monkeypatch) -> None:
    service = scheduler_loop.SchedulerService()
    options = scheduler_loop.RetryExecutionOptions("run-failed", 1, "retry-run")
    source = SimpleNamespace(
        task_id=1,
        next_retry_at=datetime.now(UTC),
        retry_count=1,
        status=TaskStatus.FAILED,
        cancel_requested_at=datetime.now(UTC),
    )
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=source)))

    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="已被取消"):
        await service._validate_retry_source(object(), 1, options)
