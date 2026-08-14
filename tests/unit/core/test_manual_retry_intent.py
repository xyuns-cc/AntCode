import importlib
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import TaskStatus

retry_module = importlib.import_module("antcode_core.application.services.scheduler.retry_service")
manual_module = importlib.import_module("antcode_core.application.services.scheduler.manual_retry_service")
outbox_module = importlib.import_module("antcode_core.application.services.scheduler.outbox_service")


class _Transaction(AbstractAsyncContextManager):
    def __init__(self, events: list[str]):
        self.connection = object()
        self.events = events

    async def __aenter__(self):
        self.events.append("begin")
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        self.events.append("rollback" if exc_type else "commit")
        return False


class _Query:
    def __init__(self, *, first=None, exists=False, update_result=1):
        self.first = AsyncMock(return_value=first)
        self.exists = AsyncMock(return_value=exists)
        self.update = AsyncMock(return_value=update_result)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


def _execution(*, next_retry_at=None):
    return SimpleNamespace(
        id=10,
        task_id=1,
        run_id="source-run",
        status=TaskStatus.FAILED,
        next_retry_at=next_retry_at,
    )


def _task():
    return SimpleNamespace(id=1, name="task")


def _patch_transaction(monkeypatch, events):
    transaction = _Transaction(events)
    monkeypatch.setattr(manual_module, "in_transaction", lambda _name: transaction)
    monkeypatch.setattr(retry_module, "get_manual_retry_event", AsyncMock(return_value=None))
    return transaction


@pytest.mark.asyncio
async def test_manual_retry_atomically_consumes_automatic_intent(monkeypatch):
    events: list[str] = []
    transaction = _patch_transaction(monkeypatch, events)
    source_query = _Query(first=_execution(next_retry_at=datetime.now(UTC)))
    clear_query = _Query(update_result=1)
    monkeypatch.setattr(
        manual_module.TaskRun,
        "filter",
        MagicMock(side_effect=[source_query, clear_query]),
    )
    monkeypatch.setattr(manual_module.Task, "filter", MagicMock(return_value=_Query(first=_task())))
    service = retry_module.RetryService()

    async def cancel(run_id):
        assert run_id == "source-run"
        events.append("cancel_redis")
        return 1

    async def enqueue(**kwargs):
        assert kwargs["event_type"] == "task_trigger"
        assert kwargs["aggregate_type"] == "manual_retry"
        assert kwargs["aggregate_id"] == "source-run"
        assert kwargs["payload"] == {"task_id": "1", "manual_retry_source_run_id": "source-run"}
        assert kwargs["public_id"] == manual_module.manual_retry_outbox_id("source-run")
        assert kwargs["connection"] is transaction.connection
        events.append("enqueue_trigger")

    service._backend.cancel = cancel
    monkeypatch.setattr(outbox_module.scheduler_outbox_service, "enqueue", enqueue)

    result = await service.manual_retry("source-run", user_id=7)

    assert result["success"] is True
    assert result["auto_intent_consumed"] is True
    assert events == ["begin", "cancel_redis", "enqueue_trigger", "commit"]
    clear_query.update.assert_awaited_once_with(next_retry_at=None)


@pytest.mark.asyncio
async def test_manual_retry_rejects_intent_already_consumed_by_automatic_retry(monkeypatch):
    events: list[str] = []
    _patch_transaction(monkeypatch, events)
    source_query = _Query(first=_execution())
    child_query = _Query(exists=True)
    monkeypatch.setattr(
        manual_module.TaskRun,
        "filter",
        MagicMock(side_effect=[source_query, child_query]),
    )
    monkeypatch.setattr(manual_module.Task, "filter", MagicMock(return_value=_Query(first=_task())))
    service = retry_module.RetryService()
    service._backend.cancel = AsyncMock()
    monkeypatch.setattr(outbox_module.scheduler_outbox_service, "enqueue", AsyncMock())

    result = await service.manual_retry("source-run", user_id=7)

    assert result == {"success": False, "error": "自动重试已创建新的执行记录，请勿重复重试"}
    assert events == ["begin", "commit"]
    service._backend.cancel.assert_not_awaited()
    outbox_module.scheduler_outbox_service.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_retry_without_automatic_intent_enqueues_once(monkeypatch):
    events: list[str] = []
    transaction = _patch_transaction(monkeypatch, events)
    source_query = _Query(first=_execution())
    child_query = _Query(exists=False)
    monkeypatch.setattr(
        manual_module.TaskRun,
        "filter",
        MagicMock(side_effect=[source_query, child_query]),
    )
    monkeypatch.setattr(manual_module.Task, "filter", MagicMock(return_value=_Query(first=_task())))
    service = retry_module.RetryService()
    service._backend.cancel = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(outbox_module.scheduler_outbox_service, "enqueue", enqueue)

    result = await service.manual_retry("source-run", user_id=7)

    assert result["auto_intent_consumed"] is False
    enqueue.assert_awaited_once_with(
        event_type="task_trigger",
        aggregate_type="manual_retry",
        aggregate_id="source-run",
        payload={"task_id": "1", "manual_retry_source_run_id": "source-run"},
        connection=transaction.connection,
        public_id=manual_module.manual_retry_outbox_id("source-run"),
    )
    service._backend.cancel.assert_not_awaited()
    assert events == ["begin", "commit"]


@pytest.mark.asyncio
async def test_duplicate_manual_retry_returns_existing_request(monkeypatch):
    events: list[str] = []
    transaction = _patch_transaction(monkeypatch, events)
    monkeypatch.setattr(manual_module.TaskRun, "filter", MagicMock(return_value=_Query(first=_execution())))
    monkeypatch.setattr(manual_module.Task, "filter", MagicMock(return_value=_Query(first=_task())))
    get_event = AsyncMock(return_value=SimpleNamespace(public_id="existing"))
    monkeypatch.setattr(retry_module, "get_manual_retry_event", get_event)
    service = retry_module.RetryService()
    service._backend.cancel = AsyncMock()
    monkeypatch.setattr(outbox_module.scheduler_outbox_service, "enqueue", AsyncMock())

    result = await service.manual_retry("source-run", user_id=7)

    assert result["success"] is True
    assert result["already_requested"] is True
    get_event.assert_awaited_once_with(manual_module.manual_retry_outbox_id("source-run"), transaction.connection)
    service._backend.cancel.assert_not_awaited()
    outbox_module.scheduler_outbox_service.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_retry_rolls_back_when_outbox_enqueue_fails(monkeypatch):
    events: list[str] = []
    _patch_transaction(monkeypatch, events)
    source_query = _Query(first=_execution(next_retry_at=datetime.now(UTC)))
    clear_query = _Query(update_result=1)
    monkeypatch.setattr(
        manual_module.TaskRun,
        "filter",
        MagicMock(side_effect=[source_query, clear_query]),
    )
    monkeypatch.setattr(manual_module.Task, "filter", MagicMock(return_value=_Query(first=_task())))
    service = retry_module.RetryService()
    service._backend.cancel = AsyncMock(return_value=1)
    monkeypatch.setattr(
        outbox_module.scheduler_outbox_service,
        "enqueue",
        AsyncMock(side_effect=RuntimeError("outbox unavailable")),
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await service.manual_retry("source-run", user_id=7)

    assert events == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_manual_retry_fails_when_intent_cas_breaks(monkeypatch):
    events: list[str] = []
    _patch_transaction(monkeypatch, events)
    source_query = _Query(first=_execution(next_retry_at=datetime.now(UTC)))
    clear_query = _Query(update_result=0)
    monkeypatch.setattr(
        manual_module.TaskRun,
        "filter",
        MagicMock(side_effect=[source_query, clear_query]),
    )
    monkeypatch.setattr(manual_module.Task, "filter", MagicMock(return_value=_Query(first=_task())))
    service = retry_module.RetryService()
    service._backend.cancel = AsyncMock()
    monkeypatch.setattr(outbox_module.scheduler_outbox_service, "enqueue", AsyncMock())

    with pytest.raises(RuntimeError, match="消费自动 intent 失败"):
        await service.manual_retry("source-run", user_id=7)

    assert events == ["begin", "rollback"]
    service._backend.cancel.assert_not_awaited()
    outbox_module.scheduler_outbox_service.enqueue.assert_not_awaited()
