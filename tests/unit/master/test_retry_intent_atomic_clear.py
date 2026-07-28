"""P1-FN-05: durable retry intent 必须在创建新 run 的同一事务内清除。"""

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_master.control import retry_intent_guard, scheduler_loop

_INTENT_RETRY_COUNT = 2


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Query:
    def __init__(self, *, first=None, update_result=1):
        self.first = AsyncMock(return_value=first)
        self.update = AsyncMock(return_value=update_result)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


def _active_task():
    return SimpleNamespace(id=1, name="task", is_active=True, status=TaskStatus.FAILED)


def _options() -> scheduler_loop.RetryExecutionOptions:
    return scheduler_loop.RetryExecutionOptions("run-failed", _INTENT_RETRY_COUNT, "retry-run")


@pytest.mark.asyncio
async def test_retry_count_is_bound_to_source_intent(monkeypatch):
    task_query = _Query(first=_active_task())
    intent_query = _Query(update_result=1)
    create = AsyncMock(return_value=SimpleNamespace(run_id="retry-run"))
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.Task, "filter", MagicMock(return_value=task_query))
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=intent_query))
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", create)
    service = scheduler_loop.SchedulerService()
    service._validate_retry_source = AsyncMock()

    await service._claim_task_run(1, "retry-run", _options())

    service._validate_retry_source.assert_awaited_once()
    assert create.await_args.kwargs["retry_count"] == _INTENT_RETRY_COUNT
    assert create.await_args.kwargs["result_data"] == {"retry_source_run_id": "run-failed"}
    # P1-FN-05: durable intent 在同一事务内被清除
    intent_query.update.assert_awaited_once_with(next_retry_at=None)


@pytest.mark.asyncio
async def test_retry_intent_cleared_inside_run_creation_transaction(monkeypatch):
    """P1-FN-05 回归：next_retry_at 的清除必须发生在创建新 run 的同一事务提交前。

    否则 source 行锁释放后、intent 清除前，并发取消端点（按
    next_retry_at__not_isnull CAS）仍可命中并对外报成功，而新 run 已在派发。
    """
    events: list[str] = []

    class _RecordingTransaction(AbstractAsyncContextManager):
        async def __aenter__(self):
            events.append("begin")
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("commit")
            return False

    task_query = _Query(first=_active_task())
    intent_query = _Query(update_result=1)

    async def record_clear(**kwargs):
        assert kwargs == {"next_retry_at": None}
        events.append("clear_intent")
        return 1

    intent_query.update = AsyncMock(side_effect=record_clear)

    async def record_create(**_kwargs):
        events.append("create_run")
        return SimpleNamespace(run_id="retry-run")

    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _RecordingTransaction())
    monkeypatch.setattr(scheduler_loop.Task, "filter", MagicMock(return_value=task_query))
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=intent_query))
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", AsyncMock(side_effect=record_create))
    service = scheduler_loop.SchedulerService()
    service._validate_retry_source = AsyncMock()

    await service._claim_task_run(1, "retry-run", _options())

    assert events == ["begin", "clear_intent", "create_run", "commit"]


@pytest.mark.asyncio
async def test_consume_retry_intent_zero_rows_raises_invalid(monkeypatch):
    """同事务行锁下 intent 不可能被并发清除；0 行属于不变量破坏，显式失败。"""
    intent_query = _Query(update_result=0)
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=intent_query))

    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="清除失败"):
        await scheduler_loop.SchedulerService._consume_retry_intent(object(), _options())
