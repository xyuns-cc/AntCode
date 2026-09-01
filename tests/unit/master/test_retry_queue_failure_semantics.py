"""Retry queue poison isolation and durable failure semantics."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_master.control import retry_intent_guard, retry_loop, scheduler_loop


class _Query:
    def __init__(self, *, first=None):
        self.first = AsyncMock(return_value=first)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


@pytest.mark.asyncio
async def test_retry_creation_failure_requeues_without_ack():
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(side_effect=RuntimeError("create failed"))
    service._backend.requeue = AsyncMock()
    service._backend.ack = AsyncMock()
    service._backend.incr_attempts = AsyncMock(return_value=1)

    result = await service._handle_claimed_item({"__raw_payload": "payload", "task_id": 1, "run_id": "run-x"})

    assert result is None
    service._backend.incr_attempts.assert_awaited_once_with("run-x")
    service._backend.requeue.assert_awaited_once_with("payload", delay_seconds=5)
    service._backend.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_infrastructure_failure_never_discards_durable_intent():
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(side_effect=RuntimeError("create failed"))
    service._backend.requeue = AsyncMock()
    service._backend.ack = AsyncMock()
    service._backend.clear_attempts = AsyncMock()
    service._backend.incr_attempts = AsyncMock(return_value=500)

    result = await service._handle_claimed_item({"__raw_payload": "payload", "task_id": 1, "run_id": "run-x"})

    assert result is None
    service._backend.requeue.assert_awaited_once_with(
        "payload",
        delay_seconds=retry_loop.INFRASTRUCTURE_REQUEUE_DELAY_SECONDS,
    )
    service._backend.ack.assert_not_awaited()
    service._backend.clear_attempts.assert_not_awaited()


@pytest.mark.asyncio
async def test_structurally_invalid_retry_payload_is_discarded() -> None:
    service = retry_loop.RetryService()
    service._backend.ack = AsyncMock()
    service._backend.clear_attempts = AsyncMock()
    service._backend.requeue = AsyncMock()

    result = await service._handle_claimed_item({"__raw_payload": "payload", "task_id": "invalid", "run_id": "run-x"})

    assert result is None
    service._backend.ack.assert_awaited_once_with("payload")
    service._backend.clear_attempts.assert_awaited_once_with("run-x")
    service._backend.requeue.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_due_discards_undecodable_payload_without_wedging_batch():
    backend = retry_loop.RetryQueueBackend()
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
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(side_effect=retry_intent_guard.RetryIntentInvalidError("invalid"))
    service._backend.requeue = AsyncMock()
    service._backend.ack = AsyncMock()

    result = await service._handle_claimed_item({"__raw_payload": "payload", "task_id": 1})

    assert result is None
    service._backend.requeue.assert_not_awaited()
    service._backend.ack.assert_awaited_once_with("payload")


@pytest.mark.asyncio
async def test_validate_retry_source_raises_dedicated_invalid_error(monkeypatch):
    service = scheduler_loop.SchedulerService()
    options = scheduler_loop.RetryExecutionOptions("run-failed", 1, "retry-run")
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=None)))
    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="retry source 无效"):
        await service._validate_retry_source(object(), 1, options)

    stale = SimpleNamespace(task_id=1, next_retry_at=None, retry_count=1, status=None, cancel_requested_at=None)
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=stale)))
    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="retry intent 已失效"):
        await service._validate_retry_source(object(), 1, options)

    cancelled = SimpleNamespace(
        task_id=1,
        next_retry_at=datetime.now(UTC),
        retry_count=1,
        status=TaskStatus.CANCELLED,
        cancel_requested_at=None,
    )
    monkeypatch.setattr(scheduler_loop.TaskRun, "filter", MagicMock(return_value=_Query(first=cancelled)))
    with pytest.raises(retry_intent_guard.RetryIntentInvalidError, match="已被取消"):
        await service._validate_retry_source(object(), 1, options)


@pytest.mark.asyncio
async def test_retry_intent_is_acked_only_after_new_run_and_db_cleanup(monkeypatch):
    events = []

    async def record(name, result=None):
        events.append(name)
        return result

    service = retry_loop.RetryService()
    monkeypatch.setattr(
        scheduler_loop.scheduler_service, "trigger_retry_intent", lambda _intent: record("trigger", "retry-run")
    )
    service._clear_durable_intent = lambda _run_id: record("clear")
    service._backend.ack = lambda _payload: record("ack")
    item = {
        "__raw_payload": "payload",
        "task_id": 1,
        "run_id": "run-failed",
        "retry_count": 1,
        "retry_time": datetime.now(UTC).isoformat(),
    }

    assert await service._process_claimed_intent(item) == "retry-run"
    assert events == ["trigger", "clear", "ack"]
