import importlib
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.domain.models.enums import TaskStatus
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
    monkeypatch.setattr(result_loop.task_run_service, "update_result", AsyncMock(return_value=True))
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
    monkeypatch.setattr(result_loop.task_run_service, "update_result", AsyncMock(return_value=True))
    loop._schedule_remote_retry = AsyncMock(side_effect=RuntimeError("database unavailable"))
    loop._retry_intent_durable_or_ineligible = AsyncMock(return_value=False)
    publish = AsyncMock()
    monkeypatch.setattr(result_loop, "publish_persisted_run_status", publish)
    message = data_pb2.TaskStatus(run_id="run-failed", status=data_pb2.STATUS_FAILED)

    ok = await loop._handle_message(message)

    assert ok is False
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_replay_claims_retry_generation_once(monkeypatch):
    source = SimpleNamespace(
        id=10,
        run_id="run-failed",
        retry_count=0,
        result_data={},
        next_retry_at=None,
    )
    query = _Query(first=source)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=query))
    service = scheduler_loop.SchedulerService()
    task = SimpleNamespace(id=1, retry_count=1, retry_delay=30)

    first = await service._claim_retry_intent(task, source)
    replay = await service._claim_retry_intent(task, source)

    assert first == replay
    assert source.retry_count == 1
    query.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_trigger_does_not_consume_another_runs_retry_count(monkeypatch):
    task = SimpleNamespace(id=1, name="task", is_active=True, status=TaskStatus.FAILED)
    task_query = _Query(first=task)
    execution = SimpleNamespace(run_id="manual-run")
    create = AsyncMock(return_value=execution)
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(scheduler_loop.Task, "filter", MagicMock(return_value=task_query))
    monkeypatch.setattr(scheduler_loop.TaskRun, "create", create)
    service = scheduler_loop.SchedulerService()
    service._validate_retry_source = AsyncMock()

    claimed = await service._claim_task_run(1, "manual-run")

    assert claimed == (task, execution)
    assert create.await_args.kwargs["retry_count"] == 0
    assert create.await_args.kwargs["result_data"] is None
    service._validate_retry_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_creation_failure_requeues_without_ack():
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(side_effect=RuntimeError("create failed"))
    service._backend.requeue = AsyncMock()
    service._backend.ack = AsyncMock()
    # V3: attempts 以 run_id 为键持久化在 Redis hash,与 payload 字节解耦
    service._backend.incr_attempts = AsyncMock(return_value=1)
    item = {"__raw_payload": "payload", "task_id": 1, "run_id": "run-x"}

    new_run_id = await service._handle_claimed_item(item)

    assert new_run_id is None
    service._backend.incr_attempts.assert_awaited_once_with("run-x")
    # V3: 原样 requeue,不再把 attempts 嵌进 payload 字节
    service._backend.requeue.assert_awaited_once_with("payload", delay_seconds=5)
    service._backend.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_creation_failure_drops_payload_after_max_attempts():
    """V3: 处理失败达到 MAX_CLAIM_ATTEMPTS 后不再 requeue,丢弃并清 DB intent。"""
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(side_effect=RuntimeError("create failed"))
    service._backend.requeue = AsyncMock()
    service._backend.ack = AsyncMock()
    service._backend.clear_attempts = AsyncMock()
    service._backend.incr_attempts = AsyncMock(return_value=retry_loop.MAX_CLAIM_ATTEMPTS)
    service._abandon_durable_intent = AsyncMock()
    item = {"__raw_payload": "payload", "task_id": 1, "run_id": "run-x"}

    new_run_id = await service._handle_claimed_item(item)

    assert new_run_id is None
    service._backend.requeue.assert_not_awaited()
    service._backend.ack.assert_awaited_once_with("payload")
    service._backend.clear_attempts.assert_awaited_once_with("run-x")
    # V3: 达上限丢弃时清 DB next_retry_at,阻止 _recover_from_db 再注入
    service._abandon_durable_intent.assert_awaited_once_with("run-x")


@pytest.mark.asyncio
async def test_claim_due_discards_undecodable_payload_without_wedging_batch():
    """V4: 单条坏 payload 不能让整批 claim_due 抛出 —— 否则同批已 claim 的好
    条目全部悬空未 ack。坏条目从 processing hash 移除(丢弃)后继续返回好条目。"""
    backend = retry_loop._RetryQueueBackend()
    redis = AsyncMock()
    redis.evalsha = AsyncMock(return_value=[b'{"task_id": 1, "run_id": "good"}', b"not-json"])
    redis.hdel = AsyncMock()
    backend._get_redis = AsyncMock(return_value=redis)
    backend._ensure_script = AsyncMock()
    backend._claim_sha = "sha"

    out = await backend.claim_due(limit=10)

    assert len(out) == 1
    assert out[0]["run_id"] == "good"
    assert out[0]["__raw_payload"] == '{"task_id": 1, "run_id": "good"}'
    redis.hdel.assert_awaited_once_with(backend.processing_key(), b"not-json")


@pytest.mark.asyncio
async def test_permanently_invalid_retry_intent_is_dropped_immediately():
    """G3: _validate_retry_source 判定永久无效的 intent 不等 N 次,立即丢弃。"""
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(
        side_effect=retry_intent_guard.RetryIntentInvalidError("retry intent 已失效: run_id=run-failed")
    )
    service._backend.requeue = AsyncMock()
    service._backend.ack = AsyncMock()
    item = {"__raw_payload": "payload", "task_id": 1}

    new_run_id = await service._handle_claimed_item(item)

    assert new_run_id is None
    service._backend.requeue.assert_not_awaited()
    service._backend.ack.assert_awaited_once_with("payload")


@pytest.mark.asyncio
async def test_validate_retry_source_raises_dedicated_invalid_error(monkeypatch):
    """G3: 两个永久失效分支抛 RetryIntentInvalidError(RuntimeError 子类兼容)。"""
    monkeypatch.setattr(scheduler_loop, "in_transaction", lambda _name: _Transaction())
    service = scheduler_loop.SchedulerService()
    options = scheduler_loop.RetryExecutionOptions("run-failed", 1, "retry-run")

    # source 不存在
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=None)))
    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="retry source 无效"):
        await service._validate_retry_source(object(), 1, options)

    # intent 已被消费(next_retry_at 已清)
    stale = SimpleNamespace(task_id=1, next_retry_at=None, retry_count=1, status=None)
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=stale)))
    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="retry intent 已失效"):
        await service._validate_retry_source(object(), 1, options)

    # P1-FN-01: source 已被用户取消(即使 next_retry_at 尚未清)也判永久无效
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    from antcode_core.domain.models.enums import TaskStatus as _TaskStatus

    cancelled = SimpleNamespace(
        task_id=1,
        next_retry_at=_datetime.now(_UTC),
        retry_count=1,
        status=_TaskStatus.CANCELLED,
    )
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=cancelled)))
    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="已被取消"):
        await service._validate_retry_source(object(), 1, options)

    assert issubclass(retry_intent_guard.RetryIntentInvalidError, RuntimeError)


@pytest.mark.asyncio
async def test_retry_intent_is_acked_only_after_new_run_and_db_cleanup(monkeypatch):
    events = []

    async def trigger(_intent):
        events.append("trigger")
        return "retry-run"

    async def clear(_source_run_id):
        events.append("clear")

    async def ack(_raw_payload):
        events.append("ack")

    service = retry_loop.RetryService()
    monkeypatch.setattr(scheduler_loop.scheduler_service, "trigger_retry_intent", trigger)
    service._clear_durable_intent = clear
    service._backend.ack = ack
    item = {
        "__raw_payload": "payload",
        "task_id": 1,
        "run_id": "run-failed",
        "retry_count": 1,
        "retry_time": datetime.now(UTC).isoformat(),
    }

    new_run_id = await service._process_claimed_intent(item)

    assert new_run_id == "retry-run"
    assert events == ["trigger", "clear", "ack"]
