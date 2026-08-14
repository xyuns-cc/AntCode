"""ResultLoop current Proto behavior."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.application.services.task_result_commit import ResultCommitOutcome
from antcode_core.domain.models import Task, TaskRun
from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus
from antcode_core.infrastructure.redis.sse_event_stream import SSEEventPublishError
from antcode_master.control import scheduler_loop
from antcode_master.ingester.result_loop import ResultLoop

result_module = importlib.import_module("antcode_master.ingester.result_loop")


def _only_first_query(row):
    """Mock ``Model.filter(...).only(...).first()`` chains."""
    return SimpleNamespace(only=MagicMock(return_value=SimpleNamespace(first=AsyncMock(return_value=row))))


def _outcome(status: RuntimeStatus, *, accepted: bool = True) -> ResultCommitOutcome:
    return ResultCommitOutcome(accepted, "run-1", status if accepted else None)


class _IdempotentDlqRedis:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], tuple] = {}

    async def eval(self, *args):
        _script, _key_count, _key, source, message_id, _maxlen, *fields = args
        identity = (source, message_id)
        self.entries.setdefault(identity, fields)
        return f"{len(self.entries)}-0"


@pytest.mark.asyncio
async def test_result_lease_validation_uses_redis_ttl_authority(monkeypatch):
    redis = object()
    is_current = AsyncMock(return_value=True)
    store = SimpleNamespace(is_current=is_current)

    def lease_store(client, *, namespace):
        assert client is redis
        assert namespace == "antcode"
        return store

    monkeypatch.setattr(result_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(result_module, "LeaseStore", lease_store)

    assert await result_module._validate_current_worker_lease("worker-1", "lease-1")

    is_current.assert_awaited_once_with("worker-1", "lease-1")


@pytest.mark.asyncio
async def test_handle_message_ignores_status_without_run_id(monkeypatch):
    update_result = AsyncMock()
    monkeypatch.setattr(result_module.task_run_service, "update_result_outcome", update_result)

    handled = await ResultLoop()._handle_message(data_pb2.TaskStatus(status=data_pb2.STATUS_COMPLETED))

    assert handled is True
    update_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_publishes_status_only_after_successful_update(monkeypatch):
    update_result = AsyncMock(return_value=_outcome(RuntimeStatus.SUCCESS))
    publish_status = AsyncMock()
    monkeypatch.setattr(result_module.task_run_service, "update_result_outcome", update_result)
    monkeypatch.setattr(result_module, "publish_persisted_run_status", publish_status)
    task_status = data_pb2.TaskStatus(
        run_id="run-1",
        worker_id="worker-1",
        status=data_pb2.STATUS_COMPLETED,
    )

    handled = await ResultLoop()._handle_message(task_status)

    assert handled is True
    publish_status.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_completed_report_retries_when_commit_outcome_is_failed(monkeypatch):
    update_result = AsyncMock(return_value=_outcome(RuntimeStatus.FAILED))
    loop = ResultLoop()
    loop._schedule_remote_retry = AsyncMock()
    monkeypatch.setattr(result_module.task_run_service, "update_result_outcome", update_result)
    monkeypatch.setattr(result_module, "publish_persisted_run_status", AsyncMock())
    task_status = data_pb2.TaskStatus(
        run_id="run-1",
        worker_id="worker-1",
        status=data_pb2.STATUS_COMPLETED,
    )

    assert await loop._handle_message(task_status) is True

    loop._schedule_remote_retry.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_rejected_ownership_outcome_never_retries(monkeypatch):
    outcome = ResultCommitOutcome(False, "run-1", None)
    loop = ResultLoop()
    loop._schedule_remote_retry = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(result_module.task_run_service, "update_result_outcome", AsyncMock(return_value=outcome))
    monkeypatch.setattr(result_module, "publish_persisted_run_status", publish)
    task_status = data_pb2.TaskStatus(
        run_id="run-1",
        worker_id="wrong-worker",
        status=data_pb2.STATUS_FAILED,
    )

    assert await loop._handle_message(task_status) is False

    loop._schedule_remote_retry.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_failure_is_not_acked_or_dead_lettered(monkeypatch):
    loop = ResultLoop(poll_interval=0)
    message = SimpleNamespace(msg_id="1-0", payload=data_pb2.TaskStatus(run_id="run-1"))
    stream = SimpleNamespace(
        xreadgroup_typed=AsyncMock(return_value=[message]),
        xack=AsyncMock(),
    )
    should_dead_letter = AsyncMock(return_value=True)
    move_to_dlq = AsyncMock(return_value=True)

    async def fail_publish(_payload):
        loop._running = False
        raise SSEEventPublishError("redis unavailable")

    loop._stream = stream
    monkeypatch.setattr(loop, "_handle_message", fail_publish)
    monkeypatch.setattr(loop, "_should_dead_letter", should_dead_letter)
    monkeypatch.setattr(loop, "_move_to_dlq", move_to_dlq)
    loop._running = True

    await loop._run_loop()

    stream.xack.assert_not_awaited()
    should_dead_letter.assert_not_awaited()
    move_to_dlq.assert_not_awaited()


@pytest.mark.asyncio
async def test_infrastructure_failure_is_never_dead_lettered_by_delivery_count(monkeypatch):
    loop = ResultLoop()
    message = SimpleNamespace(
        msg_id="1-0",
        decode_error=None,
        payload=data_pb2.TaskStatus(
            run_id="run-1",
            worker_id="worker-1",
            status=data_pb2.STATUS_COMPLETED,
        ),
    )
    handle = AsyncMock(side_effect=ConnectionError("postgres unavailable"))
    should_dead_letter = AsyncMock(return_value=True)
    move_to_dlq = AsyncMock(return_value=True)
    monkeypatch.setattr(loop, "_handle_message", handle)
    monkeypatch.setattr(loop, "_should_dead_letter", should_dead_letter)
    monkeypatch.setattr(loop, "_move_to_dlq", move_to_dlq)

    assert await loop._process_message(message) == (False, False)

    should_dead_letter.assert_not_awaited()
    move_to_dlq.assert_not_awaited()


@pytest.mark.asyncio
async def test_result_dead_letter_replay_is_idempotent(monkeypatch):
    redis = _IdempotentDlqRedis()
    monkeypatch.setattr(result_module, "get_redis_client", AsyncMock(return_value=redis))
    message = SimpleNamespace(
        msg_id="42-0",
        payload=data_pb2.TaskStatus(run_id="run-1"),
        decode_error="invalid frame",
        raw_fields={},
    )
    loop = ResultLoop(stream_key="antcode:task:result")

    assert await loop._move_to_dlq(message) is True
    assert await loop._move_to_dlq(message) is True
    assert len(redis.entries) == 1


@pytest.mark.asyncio
async def test_result_dead_letter_redacts_decoded_status(monkeypatch):
    redis = _IdempotentDlqRedis()
    monkeypatch.setattr(result_module, "get_redis_client", AsyncMock(return_value=redis))
    message = SimpleNamespace(
        msg_id="43-0",
        payload=data_pb2.TaskStatus(
            run_id="run-1",
            error_message="password=dead-letter-secret",
        ),
        decode_error="invalid status",
        raw_fields={},
    )

    assert await ResultLoop(stream_key="antcode:task:result")._move_to_dlq(message) is True
    fields = redis.entries[("antcode:task:result", "43-0")]
    entry = dict(zip(fields[::2], fields[1::2], strict=True))
    stored = data_pb2.TaskStatus.FromString(entry["payload"])
    assert stored.error_message == "password=***REDACTED***"


@pytest.mark.asyncio
async def test_schedule_remote_retry_skips_cancelled_run(monkeypatch):
    """G4: 用户已取消的 run(DB 有 CANCELLED 证据)不触发自动重试。"""
    execution = SimpleNamespace(
        id=1,
        run_id="run-cancelled",
        task_id=7,
        retry_count=0,
        result_data=None,
        status=TaskStatus.CANCELLED,
        runtime_status=RuntimeStatus.CANCELLED,
        cancel_requested_at=None,
    )
    task_filter = MagicMock()
    schedule_retry = AsyncMock()
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_only_first_query(execution)))
    monkeypatch.setattr(Task, "filter", task_filter)
    monkeypatch.setattr(scheduler_loop.scheduler_service, "_schedule_retry", schedule_retry)

    await ResultLoop()._schedule_remote_retry("run-cancelled")

    task_filter.assert_not_called()
    schedule_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_remote_retry_skips_runtime_cancelled_run(monkeypatch):
    """G4: runtime_status=CANCELLED 单独出现同样视作取消证据。"""
    execution = SimpleNamespace(
        id=1,
        run_id="run-cancelled",
        task_id=7,
        retry_count=0,
        result_data=None,
        status=TaskStatus.FAILED,
        runtime_status=RuntimeStatus.CANCELLED,
        cancel_requested_at=None,
    )
    schedule_retry = AsyncMock()
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_only_first_query(execution)))
    monkeypatch.setattr(scheduler_loop.scheduler_service, "_schedule_retry", schedule_retry)

    await ResultLoop()._schedule_remote_retry("run-cancelled")

    schedule_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_remote_retry_still_fires_for_failed_run(monkeypatch):
    """G4 回归保护: 正常远程失败仍然写 retry intent。"""
    execution = SimpleNamespace(
        id=1,
        run_id="run-failed",
        task_id=7,
        retry_count=0,
        result_data=None,
        status=TaskStatus.FAILED,
        runtime_status=RuntimeStatus.FAILED,
        cancel_requested_at=None,
    )
    task = SimpleNamespace(id=7, retry_count=3, retry_delay=30, is_active=True, name="task")
    schedule_retry = AsyncMock()
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_only_first_query(execution)))
    monkeypatch.setattr(Task, "filter", MagicMock(return_value=_only_first_query(task)))
    monkeypatch.setattr(scheduler_loop.scheduler_service, "_schedule_retry", schedule_retry)

    await ResultLoop()._schedule_remote_retry("run-failed")

    schedule_retry.assert_awaited_once_with(task, execution)
