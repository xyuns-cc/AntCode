"""Retry crash recovery, target lifecycle, pagination, and concurrency limits."""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus
from antcode_master.control import (
    retry_dispatch_recovery,
    retry_intent_guard,
    retry_loop,
    scheduler_loop,
)
from antcode_master.control.execution_parameters import RecoveryExecutionOptions


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _TaskQuery:
    def __init__(self, *, first=None, update_result=1):
        self.first = AsyncMock(return_value=first)
        self.update = AsyncMock(return_value=update_result)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


class _CountQuery:
    def __init__(self, count: int):
        self.count = AsyncMock(return_value=count)

    def using_db(self, _connection):
        return self


def _intent(*, task_id: int = 7, source_run_id: str = "source-run", retry_count: int = 2):
    return retry_intent_guard.RetryIntent(
        task_id=task_id,
        source_run_id=source_run_id,
        retry_count=retry_count,
        retry_time=datetime.now(UTC),
    )


def _retry_run(intent, *, status=TaskStatus.QUEUED, dispatch_status=DispatchStatus.PENDING):
    run_id = str(
        uuid.uuid5(
            scheduler_loop.RETRY_RUN_NAMESPACE,
            f"{intent.source_run_id}:{intent.retry_count}",
        )
    )
    return SimpleNamespace(
        run_id=run_id,
        task_id=intent.task_id,
        retry_count=intent.retry_count,
        result_data={"retry_source_run_id": intent.source_run_id},
        status=status,
        dispatch_status=dispatch_status,
        runtime_status=None,
    )


@pytest.mark.asyncio
async def test_committed_retry_run_is_resumed_after_pre_dispatch_crash(monkeypatch):
    intent = _intent()
    existing = _retry_run(intent)
    service = scheduler_loop.SchedulerService()
    service._resume_retry_run = AsyncMock(return_value=existing.run_id)
    monkeypatch.setattr(scheduler_loop.TaskRun, "get_or_none", AsyncMock(return_value=existing))

    run_id = await service.trigger_retry_intent(intent)

    assert run_id == existing.run_id
    service._resume_retry_run.assert_awaited_once_with(intent, existing)


@pytest.mark.asyncio
async def test_advanced_retry_run_is_not_dispatched_again(monkeypatch):
    intent = _intent()
    existing = _retry_run(
        intent,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.DISPATCHED,
    )
    service = scheduler_loop.SchedulerService()
    service._resume_retry_run = AsyncMock()
    monkeypatch.setattr(scheduler_loop.TaskRun, "get_or_none", AsyncMock(return_value=existing))

    run_id = await service.trigger_retry_intent(intent)

    assert run_id == existing.run_id
    service._resume_retry_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_retry_run_must_match_intent(monkeypatch):
    intent = _intent()
    existing = _retry_run(intent)
    existing.result_data = {"retry_source_run_id": "another-source"}
    service = scheduler_loop.SchedulerService()
    monkeypatch.setattr(scheduler_loop.TaskRun, "get_or_none", AsyncMock(return_value=existing))

    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="不匹配"):
        await service.trigger_retry_intent(intent)


@pytest.mark.asyncio
async def test_resume_uses_existing_execution_in_dispatch_pipeline(monkeypatch):
    intent = _intent()
    existing = _retry_run(intent)
    task = SimpleNamespace(id=intent.task_id, name="retry-task", is_active=True)
    task_info = {"task": task, "project": object(), "project_detail": object()}
    service = scheduler_loop.SchedulerService()
    service._run_prepared_execution = AsyncMock(return_value=existing.run_id)
    monkeypatch.setattr(retry_dispatch_recovery, "require_fencing_token", AsyncMock(return_value=7))
    monkeypatch.setattr(
        retry_dispatch_recovery,
        "take_over_pre_dispatch_run",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        retry_dispatch_recovery.relation_service,
        "get_task_with_project",
        AsyncMock(return_value=task_info),
    )

    run_id = await service._resume_retry_run(intent, existing)

    assert run_id == existing.run_id
    prepared = service._run_prepared_execution.await_args.args[1]
    assert prepared[3] is existing


@pytest.mark.asyncio
async def test_missing_retry_target_is_terminal_not_busy(monkeypatch):
    service = scheduler_loop.SchedulerService()
    options = retry_intent_guard.RetryExecutionOptions("source-run", 2, "retry-run")
    service._validate_retry_source = AsyncMock()
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop, "require_scheduler_authority", AsyncMock())
    monkeypatch.setattr(
        scheduler_loop.relation_service,
        "get_task_with_project",
        AsyncMock(return_value=None),
    )

    with pytest.raises(retry_intent_guard.RetryTargetInvalidError, match="不存在"):
        await service._prepare_execution(7, "retry-run", options)

    service._validate_retry_source.assert_awaited_once()


@pytest.mark.asyncio
async def test_inactive_retry_target_is_terminal(monkeypatch):
    task = SimpleNamespace(id=7, name="inactive", is_active=False)
    service = scheduler_loop.SchedulerService()
    service._validate_retry_source = AsyncMock()
    options = retry_intent_guard.RetryExecutionOptions("source-run", 2, "retry-run")
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop, "require_scheduler_authority", AsyncMock())
    monkeypatch.setattr(
        scheduler_loop.Task,
        "filter",
        MagicMock(return_value=_TaskQuery(first=task)),
    )

    with pytest.raises(retry_intent_guard.RetryTargetInvalidError, match="未激活"):
        await service._claim_task_run(task.id, "retry-run", options, scheduler_fencing_token=7)


@pytest.mark.asyncio
async def test_retry_consumption_locks_task_before_source_run(monkeypatch):
    events: list[str] = []
    task = SimpleNamespace(id=7, name="task", is_active=True, max_instances=1)

    class _OrderedTaskQuery(_TaskQuery):
        def select_for_update(self):
            events.append("task")
            return self

    service = scheduler_loop.SchedulerService()
    service._validate_retry_source = AsyncMock(side_effect=lambda *_args: events.append("source"))
    service._consume_retry_intent = AsyncMock()
    service._count_active_runs = AsyncMock(return_value=0)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop, "require_scheduler_authority", AsyncMock())
    monkeypatch.setattr(
        scheduler_loop.Task,
        "filter",
        MagicMock(return_value=_OrderedTaskQuery(first=task)),
    )
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", AsyncMock(return_value=SimpleNamespace()))
    options = retry_intent_guard.RetryExecutionOptions("source-run", 1, "retry-run")

    await service._claim_task_run(task.id, "retry-run", options, scheduler_fencing_token=7)

    assert events == ["task", "source"]


@pytest.mark.asyncio
async def test_busy_recovery_claim_leaves_source_run_unchanged(monkeypatch):
    task = SimpleNamespace(id=7, name="task", is_active=True, max_instances=1)
    service = scheduler_loop.SchedulerService()
    service._count_active_runs = AsyncMock(return_value=2)
    source = SimpleNamespace(id=8, task_id=task.id, status=TaskStatus.RUNNING)
    lock_source = AsyncMock(return_value=source)
    transition = AsyncMock()
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop, "require_scheduler_authority", AsyncMock())
    monkeypatch.setattr(
        scheduler_loop.Task,
        "filter",
        MagicMock(return_value=_TaskQuery(first=task)),
    )
    monkeypatch.setattr(scheduler_loop, "lock_interrupted_source", lock_source)
    monkeypatch.setattr(scheduler_loop, "transition_interrupted_source", transition)
    create = AsyncMock()
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", create)
    options = RecoveryExecutionOptions("source-run", {"_resume": True})

    claimed = await service._claim_task_run(
        task.id,
        "recovery-run",
        scheduler_fencing_token=7,
        recovery_options=options,
    )

    assert claimed is None
    lock_source.assert_awaited_once()
    transition.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_target_clears_durable_intent_before_ack(monkeypatch):
    events: list[str] = []
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(side_effect=retry_intent_guard.RetryTargetInvalidError("task inactive"))
    terminate = AsyncMock(side_effect=lambda _run_id: events.append("clear"))
    service._discard_claimed = AsyncMock(side_effect=lambda *_args: events.append("ack"))
    service._backend.requeue = AsyncMock()
    monkeypatch.setattr(retry_loop, "terminate_durable_intent", terminate)

    result = await service._handle_claimed_item({"__raw_payload": "payload", "task_id": 7, "run_id": "source-run"})

    assert result is None
    assert events == ["clear", "ack"]
    service._backend.requeue.assert_not_awaited()


@pytest.mark.asyncio
async def test_max_instances_counts_runs_even_when_parent_status_is_terminal(monkeypatch):
    task = SimpleNamespace(
        id=7,
        name="task",
        is_active=True,
        status=TaskStatus.FAILED,
        max_instances=1,
    )
    service = scheduler_loop.SchedulerService()
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop, "require_scheduler_authority", AsyncMock())
    monkeypatch.setattr(
        scheduler_loop.Task,
        "filter",
        MagicMock(return_value=_TaskQuery(first=task)),
    )
    active_query = _CountQuery(1)
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=active_query))
    create = AsyncMock()
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", create)

    claimed = await service._claim_task_run(task.id, "new-run", scheduler_fencing_token=7)

    assert claimed is None
    active_query.count.assert_awaited_once()
    create.assert_not_awaited()
