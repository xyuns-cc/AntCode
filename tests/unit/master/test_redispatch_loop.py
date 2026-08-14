import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import tortoise.transactions as transactions
from antcode_core.domain.models.enums import DispatchStatus
from antcode_master.control.redispatch_loop import RedispatchLoop

loop_module = importlib.import_module("antcode_master.control.redispatch_loop")
status_module = importlib.import_module("antcode_core.application.services.scheduler.execution_status_service")
dispatcher_module = importlib.import_module("antcode_core.application.services.workers.worker_dispatcher")
INITIAL_ATTEMPTS = 2
EXHAUSTED_ATTEMPTS = INITIAL_ATTEMPTS + 1
RETRY_DELAY_SECONDS = 6
EXPECTED_AUDIT_ATTEMPTS = 2
# B12: 补派在本进程的 Leader 代际下派发；这里替掉 Redis 选主，只保留代际来源。
LEADER_EPOCH = 11


@pytest.fixture(autouse=True)
def leader_epoch(monkeypatch):
    monkeypatch.setattr(loop_module, "require_fencing_token", AsyncMock(return_value=LEADER_EPOCH))


class _RedispatchStore:
    def __init__(self, due, *, enqueue_result=True):
        self.due = due
        self.enqueue_result = enqueue_result
        self.acked = []
        self.requeued = []
        self.enqueued = []
        self.sweeps = 0

    async def sweep_stalled(self):
        self.sweeps += 1

    async def claim_due(self, *, limit):
        assert limit == 50
        return self.due

    async def ack(self, raw_payload):
        self.acked.append(raw_payload)

    async def requeue_raw(self, raw_payload, *, delay_seconds):
        self.requeued.append((raw_payload, delay_seconds))

    async def enqueue(self, **payload):
        self.enqueued.append(payload)
        return self.enqueue_result


class _Dispatcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.requests = []

    async def dispatch_task(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


class _StatusService:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.updates = []

    async def update_dispatch_status(self, **update):
        self.updates.append(update)
        if self.error:
            raise self.error
        return self.result


class _Transaction:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Query:
    def __init__(self, *, exists=False):
        self._exists = exists

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self

    async def first(self):
        return SimpleNamespace()

    async def exists(self):
        return self._exists


def _mock_audit_transaction(monkeypatch, *, exists=False):
    from antcode_core.domain.models.audit_log import AuditLog

    monkeypatch.setattr(transactions, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(loop_module.TaskRun, "filter", lambda **_criteria: _Query())
    monkeypatch.setattr(AuditLog, "filter", lambda **_criteria: _Query(exists=exists))


def _payload():
    return {
        "__raw_payload": "raw-1",
        "run_id": "run-1",
        "task_id": 3,
        "project_id": "project-1",
        "attempts": INITIAL_ATTEMPTS,
        "timeout": 90,
        "region": "cn-east",
        "require_render": True,
    }


@pytest.mark.asyncio
async def test_successful_redispatch_is_acked_after_dispatch(monkeypatch):
    store = _RedispatchStore([_payload()])
    dispatcher = _Dispatcher(SimpleNamespace(success=True, worker_name="worker-1"))
    monkeypatch.setattr(loop_module, "redispatch_service", store)
    monkeypatch.setattr(dispatcher_module, "worker_task_dispatcher", dispatcher)

    await RedispatchLoop(tick_interval_seconds=2)._tick()

    assert store.sweeps == 1
    assert store.acked == ["raw-1"]
    assert store.requeued == []
    assert dispatcher.requests[0]["run_id"] == "run-1"
    assert dispatcher.requests[0]["timeout"] == 90
    assert dispatcher.requests[0]["region"] == "cn-east"
    assert dispatcher.requests[0]["require_render"] is True


@pytest.mark.asyncio
async def test_dispatch_exception_requeues_without_ack(monkeypatch):
    store = _RedispatchStore([_payload()])
    dispatcher = _Dispatcher(error=RuntimeError("gateway down"))
    monkeypatch.setattr(loop_module, "redispatch_service", store)
    monkeypatch.setattr(dispatcher_module, "worker_task_dispatcher", dispatcher)

    await RedispatchLoop(tick_interval_seconds=4)._tick()

    assert store.acked == []
    assert store.requeued == [("raw-1", 4)]


@pytest.mark.asyncio
async def test_failed_dispatch_reenqueues_before_old_payload_ack(monkeypatch):
    store = _RedispatchStore([_payload()])
    dispatcher = _Dispatcher(SimpleNamespace(success=False, error="no worker"))
    monkeypatch.setattr(loop_module, "redispatch_service", store)
    monkeypatch.setattr(dispatcher_module, "worker_task_dispatcher", dispatcher)

    await RedispatchLoop()._tick()

    assert store.enqueued[0]["attempts"] == EXHAUSTED_ATTEMPTS
    assert store.enqueued[0]["reason"] == "no worker"
    assert store.enqueued[0]["region"] == "cn-east"
    assert store.enqueued[0]["require_render"] is True
    assert store.acked == ["raw-1"]


@pytest.mark.asyncio
async def test_give_up_state_failure_prevents_ack(monkeypatch):
    store = _RedispatchStore([_payload()], enqueue_result=False)
    dispatcher = _Dispatcher(SimpleNamespace(success=False, error="no worker"))
    status = _StatusService(error=RuntimeError("postgres down"))
    monkeypatch.setattr(loop_module, "redispatch_service", store)
    monkeypatch.setattr(dispatcher_module, "worker_task_dispatcher", dispatcher)
    monkeypatch.setattr(status_module, "execution_status_service", status)

    await RedispatchLoop(tick_interval_seconds=6)._tick()

    assert status.updates[0]["status"] == DispatchStatus.FAILED
    assert store.acked == []
    assert store.requeued == [("raw-1", 6)]


@pytest.mark.asyncio
async def test_unpersisted_give_up_state_raises(monkeypatch):
    status = _StatusService(result=False)

    async def missing(**_criteria):
        return None

    monkeypatch.setattr(status_module, "execution_status_service", status)
    monkeypatch.setattr(loop_module.TaskRun, "get_or_none", missing)

    with pytest.raises(RuntimeError, match="状态未持久化"):
        await RedispatchLoop._mark_failed("run-1", 4, "no worker")


@pytest.mark.asyncio
async def test_terminal_redispatch_replay_is_not_marked_again(monkeypatch):
    status = _StatusService(result=False)

    async def terminal(**_criteria):
        return SimpleNamespace(dispatch_status=DispatchStatus.ACKED)

    monkeypatch.setattr(status_module, "execution_status_service", status)
    monkeypatch.setattr(loop_module.TaskRun, "get_or_none", terminal)

    owned = await RedispatchLoop._mark_failed("run-1", 4, "no worker")

    assert owned is False


@pytest.mark.asyncio
async def test_give_up_audit_uses_model_fields_and_exposes_failure(monkeypatch):
    from antcode_core.domain.models.audit_log import AuditLog
    from antcode_core.domain.models.enums import AuditAction

    audit_create = AsyncMock()
    _mock_audit_transaction(monkeypatch)
    monkeypatch.setattr(AuditLog, "create", audit_create)

    await RedispatchLoop._write_give_up_audit(
        _payload(),
        reason="token=secret-value-123",
        attempts=EXHAUSTED_ATTEMPTS,
    )

    fields = audit_create.await_args.kwargs
    assert fields["action"] == AuditAction.REDISPATCH_GIVE_UP
    assert fields["username"] == "system"
    assert fields["new_value"] == {
        "attempts": EXHAUSTED_ATTEMPTS,
        "project_id": "project-1",
    }
    assert fields["success"] is False
    assert fields["new_value"]["attempts"] == EXHAUSTED_ATTEMPTS
    assert "secret-value-123" not in fields["error_message"]


@pytest.mark.asyncio
async def test_give_up_audit_failure_is_retried_after_failed_state_persists(monkeypatch):
    from antcode_core.application.services.alert import alert_service
    from antcode_core.domain.models.audit_log import AuditLog

    store = _RedispatchStore([_payload()], enqueue_result=False)
    dispatcher = _Dispatcher(SimpleNamespace(success=False, error="no worker"))
    status = SimpleNamespace(update_dispatch_status=AsyncMock(side_effect=[True, False]))
    audit_create = AsyncMock(side_effect=[RuntimeError("audit unavailable"), SimpleNamespace(id=1)])

    async def persisted_failed(**_criteria):
        return SimpleNamespace(
            dispatch_status=DispatchStatus.FAILED,
            error_message=f"补派耗尽 ({EXHAUSTED_ATTEMPTS}次): no worker",
        )

    monkeypatch.setattr(loop_module, "redispatch_service", store)
    monkeypatch.setattr(dispatcher_module, "worker_task_dispatcher", dispatcher)
    monkeypatch.setattr(status_module, "execution_status_service", status)
    monkeypatch.setattr(loop_module.TaskRun, "get_or_none", persisted_failed)
    _mock_audit_transaction(monkeypatch)
    monkeypatch.setattr(AuditLog, "create", audit_create)
    monkeypatch.setattr(alert_service, "send_alert", AsyncMock())

    loop = RedispatchLoop(tick_interval_seconds=RETRY_DELAY_SECONDS)
    await loop._tick()
    await loop._tick()

    assert store.requeued == [("raw-1", RETRY_DELAY_SECONDS)]
    assert store.acked == ["raw-1"]
    assert audit_create.await_count == EXPECTED_AUDIT_ATTEMPTS


@pytest.mark.asyncio
async def test_existing_give_up_audit_makes_replay_idempotent(monkeypatch):
    from antcode_core.domain.models.audit_log import AuditLog

    audit_create = AsyncMock()
    _mock_audit_transaction(monkeypatch, exists=True)
    monkeypatch.setattr(AuditLog, "create", audit_create)

    await RedispatchLoop._write_give_up_audit(
        _payload(),
        reason="no worker",
        attempts=EXHAUSTED_ATTEMPTS,
    )

    audit_create.assert_not_awaited()
