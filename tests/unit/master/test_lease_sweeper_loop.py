import asyncio
import importlib

import pytest
from antcode_master.control.lease_sweeper_loop import (
    AUDIT_SECURITY_MAXLEN,
    AUDIT_SECURITY_STREAM,
    LeaseSweeperLoop,
)

loop_module = importlib.import_module("antcode_master.control.lease_sweeper_loop")


class _LeaseStore:
    namespace = "test"

    def __init__(self, evicted=None, error=None):
        self.evicted = evicted or []
        self.error = error
        self.calls = []
        self.called = asyncio.Event()

    async def sweep_expired(self, *, batch):
        self.calls.append(batch)
        self.called.set()
        if self.error:
            raise self.error
        return self.evicted


class _AuditStream:
    def __init__(self, error=None):
        self.error = error
        self.entries = []

    async def xadd(self, stream, fields, **options):
        if self.error:
            raise self.error
        self.entries.append((stream, fields, options))


class _LogRecorder:
    def __init__(self):
        self.exceptions = []
        self.errors = []
        self.active_exception = None

    def exception(self, message):
        self.exceptions.append(message)

    def info(self, *_args):
        return None

    def warning(self, *_args):
        return None

    def opt(self, *, exception):
        self.active_exception = exception
        return self

    def error(self, message):
        self.errors.append((message, self.active_exception))


@pytest.mark.asyncio
async def test_eviction_callback_and_audit_preserve_lease_generation():
    handled = []
    audit = _AuditStream()

    async def on_evicted(worker_id, lease_id):
        handled.append((worker_id, lease_id))

    loop = LeaseSweeperLoop(_LeaseStore(), on_evicted, audit)
    await loop._handle_evictions([("worker-1", "lease-7")])

    assert handled == [("worker-1", "lease-7")]
    stream, fields, options = audit.entries[0]
    assert stream == AUDIT_SECURITY_STREAM
    assert fields["worker_id"] == "worker-1"
    assert fields["lease_id"] == "lease-7"
    assert fields["reason"] == "lease_expired"
    assert fields["ts"].isdigit()
    assert options == {"maxlen": AUDIT_SECURITY_MAXLEN, "approximate": True}


@pytest.mark.asyncio
async def test_audit_dependency_failure_is_explicitly_logged(monkeypatch):
    logs = _LogRecorder()
    loop = LeaseSweeperLoop(_LeaseStore(), audit_stream=_AuditStream(RuntimeError("redis down")))
    monkeypatch.setattr(loop_module, "logger", logs)

    await loop._emit_audit("worker-1", "lease-7")

    assert logs.exceptions == ["写 worker_evicted audit 失败: worker_id=worker-1"]


@pytest.mark.asyncio
async def test_callback_failure_is_logged_without_losing_audit(monkeypatch):
    logs = _LogRecorder()
    audit = _AuditStream()

    async def fail_callback(_worker_id, _lease_id):
        raise RuntimeError("postgres down")

    monkeypatch.setattr(loop_module, "logger", logs)
    loop = LeaseSweeperLoop(_LeaseStore(), fail_callback, audit)

    await loop._handle_evictions([("worker-1", "lease-7")])

    assert audit.entries[0][1]["lease_id"] == "lease-7"
    message, error = logs.errors[0]
    assert message == "on_worker_evicted 回调异常: worker_id=worker-1"
    assert str(error) == "postgres down"


@pytest.mark.asyncio
async def test_sweep_failure_is_recorded_and_stop_cancels_loop(monkeypatch):
    store = _LeaseStore(error=RuntimeError("redis down"))
    logs = _LogRecorder()

    async def leader():
        return True

    monkeypatch.setattr(loop_module, "ensure_leader", leader)
    monkeypatch.setattr(loop_module, "logger", logs)
    loop = LeaseSweeperLoop(store, interval=0.1, batch=7)
    await loop.start()
    await asyncio.wait_for(store.called.wait(), timeout=1)
    await loop.stop()

    assert store.calls == [7]
    assert logs.exceptions == ["LeaseSweeperLoop sweep 异常"]
    assert loop.is_running is False
    assert loop._task is None
