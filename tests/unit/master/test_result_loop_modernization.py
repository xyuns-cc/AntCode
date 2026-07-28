"""ResultLoop current Proto behavior."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.domain.models import Task, TaskRun
from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus
from antcode_core.infrastructure.redis.sse_event_stream import SSEEventPublishError
from antcode_master.control import scheduler_loop
from antcode_master.ingester.result_loop import ResultLoop

result_module = importlib.import_module("antcode_master.ingester.result_loop")


def _only_first_query(row):
    """Mock ``Model.filter(...).only(...).first()`` chains."""
    return SimpleNamespace(only=MagicMock(return_value=SimpleNamespace(first=AsyncMock(return_value=row))))


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
    monkeypatch.setattr(result_module.task_run_service, "update_result", update_result)

    handled = await ResultLoop()._handle_message(data_pb2.TaskStatus(status=data_pb2.STATUS_COMPLETED))

    assert handled is True
    update_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_publishes_status_only_after_successful_update(monkeypatch):
    update_result = AsyncMock(return_value=True)
    publish_status = AsyncMock()
    monkeypatch.setattr(result_module.task_run_service, "update_result", update_result)
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
    )
    task = SimpleNamespace(id=7, retry_count=3, retry_delay=30, is_active=True, name="task")
    schedule_retry = AsyncMock()
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_only_first_query(execution)))
    monkeypatch.setattr(Task, "filter", MagicMock(return_value=_only_first_query(task)))
    monkeypatch.setattr(scheduler_loop.scheduler_service, "_schedule_retry", schedule_retry)

    await ResultLoop()._schedule_remote_retry("run-failed")

    schedule_retry.assert_awaited_once_with(task, execution)
