import importlib
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.application.services.task_result_commit import ResultCommitOutcome
from antcode_core.domain.models import Task, TaskRun
from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus
from antcode_master.control import retry_intent_guard, retry_loop, scheduler_loop

result_loop = importlib.import_module("antcode_master.ingester.result_loop")


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

    def only(self, *_fields):
        return self


def _lock_retry_task(monkeypatch, task):
    monkeypatch.setattr(scheduler_loop.Task, "filter", MagicMock(return_value=_Query(first=task)))


@pytest.mark.asyncio
async def test_follower_result_consumer_persists_retry_intent(monkeypatch):
    service = scheduler_loop.SchedulerService()
    retry_time = datetime.now(UTC)
    intent = scheduler_loop.RetryIntent(1, "run-failed", 1, retry_time)
    service._claim_retry_intent = AsyncMock(return_value=intent)
    service._log_execution = AsyncMock()
    schedule_intent = AsyncMock()
    ensure_leader = AsyncMock(return_value=False)
    monkeypatch.setattr(retry_loop.retry_service, "schedule_intent", schedule_intent)
    monkeypatch.setattr(scheduler_loop, "ensure_leader", ensure_leader)

    await service._schedule_retry(
        SimpleNamespace(id=1),
        SimpleNamespace(run_id="run-failed"),
    )

    ensure_leader.assert_not_awaited()
    schedule_intent.assert_awaited_once_with(intent)


@pytest.mark.asyncio
async def test_retry_scheduling_failure_does_not_deadletter_persisted_result(monkeypatch):
    """V5: 结果已持久化时,retry 调度(Redis 入队)失败不应让消息进 DLQ。

    _schedule_retry 是 durable-first(先写 next_retry_at 再投 Redis),入队失败
    可由 _recover_from_db 恢复;因此捕获异常后照常 ACK + 推送状态,不再传播到
    dead-letter 路径丢失已记录的结果。
    """
    loop = result_loop.ResultLoop()
    outcome = ResultCommitOutcome(True, "run-failed", RuntimeStatus.FAILED)
    monkeypatch.setattr(result_loop.task_run_service, "update_result_outcome", AsyncMock(return_value=outcome))
    loop._schedule_remote_retry = AsyncMock(side_effect=RuntimeError("database unavailable"))
    # P1-FN-04: durable intent 已落库（可由 _recover_from_db 接管）→ 照常 ACK
    loop._retry_intent_durable_or_ineligible = AsyncMock(return_value=True)
    publish = AsyncMock()
    monkeypatch.setattr(result_loop, "publish_persisted_run_status", publish)
    message = data_pb2.TaskStatus(run_id="run-failed", status=data_pb2.STATUS_FAILED)

    ok = await loop._handle_message(message)

    assert ok is True
    loop._schedule_remote_retry.assert_awaited_once()
    loop._retry_intent_durable_or_ineligible.assert_awaited_once_with("run-failed")
    publish.assert_awaited_once_with("run-failed")


@pytest.mark.asyncio
async def test_retry_scheduling_failure_without_durable_intent_keeps_pel(monkeypatch):
    """P1-FN-04: durable intent 未落库且 run 需要重试时不得 ACK ——
    结果消息保留 PEL 重放，自动重试不允许静默丢失。"""
    loop = result_loop.ResultLoop()
    outcome = ResultCommitOutcome(True, "run-failed", RuntimeStatus.FAILED)
    monkeypatch.setattr(result_loop.task_run_service, "update_result_outcome", AsyncMock(return_value=outcome))
    loop._schedule_remote_retry = AsyncMock(side_effect=RuntimeError("database unavailable"))
    loop._retry_intent_durable_or_ineligible = AsyncMock(return_value=False)
    publish = AsyncMock()
    monkeypatch.setattr(result_loop, "publish_persisted_run_status", publish)
    message = data_pb2.TaskStatus(run_id="run-failed", status=data_pb2.STATUS_FAILED)

    with pytest.raises(result_loop.RetryIntentNotDurableError, match="尚未耐久"):
        await loop._handle_message(message)
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_durable_retry_intent_never_enters_poison_dlq(monkeypatch):
    loop = result_loop.ResultLoop()
    outcome = ResultCommitOutcome(True, "run-failed", RuntimeStatus.FAILED)
    monkeypatch.setattr(result_loop.task_run_service, "update_result_outcome", AsyncMock(return_value=outcome))
    loop._schedule_remote_retry = AsyncMock(side_effect=RuntimeError("postgres unavailable"))
    loop._retry_intent_durable_or_ineligible = AsyncMock(return_value=False)
    loop._should_dead_letter = AsyncMock(return_value=True)
    loop._move_to_dlq = AsyncMock(return_value=True)
    message = SimpleNamespace(
        msg_id="1-0",
        decode_error=None,
        payload=data_pb2.TaskStatus(run_id="run-failed", status=data_pb2.STATUS_FAILED),
    )

    assert await loop._process_message(message) == (False, False)

    loop._should_dead_letter.assert_not_awaited()
    loop._move_to_dlq.assert_not_awaited()


@pytest.mark.asyncio
async def test_postgres_retry_intent_is_sufficient_recovery_evidence(monkeypatch):
    execution = SimpleNamespace(
        task_id=1,
        retry_count=1,
        next_retry_at=datetime.now(UTC),
        status=TaskStatus.FAILED,
        runtime_status=RuntimeStatus.FAILED,
        cancel_requested_at=None,
    )
    task_filter = MagicMock()
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_Query(first=execution)))
    monkeypatch.setattr(Task, "filter", task_filter)

    assert await result_loop.ResultLoop()._retry_intent_durable_or_ineligible("run-failed") is True

    task_filter.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_replay_claims_retry_generation_once(monkeypatch):
    source = SimpleNamespace(
        id=10,
        run_id="run-failed",
        retry_count=0,
        result_data={},
        next_retry_at=None,
        cancel_requested_at=None,
    )
    query = _Query(first=source)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=query))
    service = scheduler_loop.SchedulerService()
    task = SimpleNamespace(id=1, retry_count=1, retry_delay=30)
    _lock_retry_task(monkeypatch, task)

    first = await service._claim_retry_intent(task, source)
    replay = await service._claim_retry_intent(task, source)

    assert first == replay
    assert source.retry_count == 1
    query.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumed_retry_intent_history_blocks_terminal_replay(monkeypatch):
    source = SimpleNamespace(
        id=10,
        run_id="run-failed",
        retry_count=1,
        result_data={
            "retry_intent": {
                "source_run_id": "run-failed",
                "retry_count": 1,
                "retry_time": datetime.now(UTC).isoformat(),
            }
        },
        next_retry_at=None,
        cancel_requested_at=None,
    )
    query = _Query(first=source)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=query))
    task = SimpleNamespace(id=1, retry_count=3, retry_delay=30)
    _lock_retry_task(monkeypatch, task)

    intent = await scheduler_loop.SchedulerService()._claim_retry_intent(
        task,
        source,
    )

    assert intent is None
    assert source.retry_count == 1
    query.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_cancellation_history_blocks_terminal_replay(monkeypatch):
    source = SimpleNamespace(
        id=10,
        run_id="run-failed",
        retry_count=1,
        result_data={
            "retry_cancellation": {
                "cancelled_by_user_id": 7,
                "cancelled_at": datetime.now(UTC).isoformat(),
            }
        },
        next_retry_at=None,
        cancel_requested_at=None,
    )
    query = _Query(first=source)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=query))
    task = SimpleNamespace(id=1, retry_count=3, retry_delay=30)
    _lock_retry_task(monkeypatch, task)

    intent = await scheduler_loop.SchedulerService()._claim_retry_intent(
        task,
        source,
    )

    assert intent is None
    query.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_trigger_does_not_consume_another_runs_retry_count(monkeypatch):
    task = SimpleNamespace(id=1, name="task", is_active=True, status=TaskStatus.FAILED)
    task_query = _Query(first=task)
    execution = SimpleNamespace(run_id="manual-run")
    create = AsyncMock(return_value=execution)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.Task, "filter", MagicMock(return_value=task_query))
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", create)
    monkeypatch.setattr(scheduler_loop, "require_scheduler_authority", AsyncMock())
    service = scheduler_loop.SchedulerService()
    service._validate_retry_source = AsyncMock()
    service._count_active_runs = AsyncMock(return_value=0)

    claimed = await service._claim_task_run(1, "manual-run", scheduler_fencing_token=7)

    assert claimed == (task, execution)
    assert create.await_args.kwargs["retry_count"] == 0
    assert create.await_args.kwargs["result_data"] is None
    service._validate_retry_source.assert_not_awaited()
