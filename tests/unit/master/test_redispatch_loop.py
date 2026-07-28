import importlib
from types import SimpleNamespace

import pytest
from antcode_core.domain.models.enums import DispatchStatus
from antcode_master.control.redispatch_loop import RedispatchLoop

loop_module = importlib.import_module("antcode_master.control.redispatch_loop")
status_module = importlib.import_module("antcode_core.application.services.scheduler.execution_status_service")
dispatcher_module = importlib.import_module("antcode_core.application.services.workers.worker_dispatcher")


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


def _payload():
    return {
        "__raw_payload": "raw-1",
        "run_id": "run-1",
        "task_id": 3,
        "project_id": "project-1",
        "attempts": 2,
        "timeout": 90,
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

    assert store.enqueued[0]["attempts"] == 3
    assert store.enqueued[0]["reason"] == "no worker"
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

    persisted = await RedispatchLoop._mark_failed("run-1", 4, "no worker")

    assert persisted is False
