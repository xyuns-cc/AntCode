import importlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from antcode_master.control.reconcile_loop import ReconcileLoop

loop_module = importlib.import_module("antcode_master.control.reconcile_loop")
repairs_module = importlib.import_module("antcode_master.control.reconcile_repairs")


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.fields = ()

    def only(self, *fields):
        self.fields = fields
        return self

    async def all(self):
        return self.rows


class _TaskRuns:
    success = []
    failed = []
    zombies = []
    filters = []

    @classmethod
    def reset(cls):
        cls.success = []
        cls.failed = []
        cls.zombies = []
        cls.filters = []

    @classmethod
    def filter(cls, **criteria):
        cls.filters.append(criteria)
        if criteria.get("status") == TaskStatus.PENDING:
            return _Query(cls.zombies)
        if criteria.get("error_message__isnull"):
            return _Query(cls.success)
        return _Query(cls.failed)


class _StatusService:
    def __init__(self, error=None):
        self.error = error
        self.runtime_updates = []
        self.dispatch_updates = []

    async def update_runtime_status(self, **update):
        if self.error:
            raise self.error
        self.runtime_updates.append(update)
        return True

    async def update_dispatch_status(self, **update):
        if self.error:
            raise self.error
        self.dispatch_updates.append(update)
        return True


class _LogRecorder:
    def __init__(self):
        self.exceptions = []

    def exception(self, message):
        self.exceptions.append(message)

    def warning(self, _message):
        return None

    def info(self, _message):
        return None


@pytest.fixture(autouse=True)
def _fake_task_runs(monkeypatch):
    _TaskRuns.reset()
    monkeypatch.setattr(repairs_module, "TaskRun", _TaskRuns)


@pytest.mark.asyncio
async def test_inconsistent_runs_update_runtime_and_overall_state(monkeypatch):
    ended_at = datetime.now(UTC)
    _TaskRuns.success = [SimpleNamespace(run_id="ok", end_time=ended_at)]
    _TaskRuns.failed = [SimpleNamespace(run_id="bad", end_time=ended_at, error_message="exit 1")]
    service = _StatusService()
    monkeypatch.setattr(repairs_module, "execution_status_service", service)

    await ReconcileLoop()._check_inconsistent_states(11)

    assert [item["status"] for item in service.runtime_updates] == [
        RuntimeStatus.SUCCESS,
        RuntimeStatus.FAILED,
    ]
    assert [item["run_id"] for item in service.runtime_updates] == ["ok", "bad"]
    assert service.runtime_updates[0]["status_at"] == ended_at
    assert service.runtime_updates[1]["error_message"] == "exit 1"


@pytest.mark.asyncio
async def test_zombie_cleanup_uses_dispatch_cas(monkeypatch):
    _TaskRuns.zombies = [SimpleNamespace(run_id="stale-run")]
    service = _StatusService()
    monkeypatch.setattr(repairs_module, "execution_status_service", service)

    await ReconcileLoop()._cleanup_zombie_tasks(12)

    assert len(service.dispatch_updates) == 1
    update = service.dispatch_updates[0]
    assert update["run_id"] == "stale-run"
    assert update["status"] == DispatchStatus.FAILED
    assert update["error_message"] == "任务长时间未调度，已清理"
    assert update["status_at"].tzinfo == UTC


@pytest.mark.asyncio
async def test_state_service_failure_is_recorded(monkeypatch):
    _TaskRuns.zombies = [SimpleNamespace(run_id="stale-run")]
    service = _StatusService(RuntimeError("postgres down"))
    logs = _LogRecorder()
    monkeypatch.setattr(repairs_module, "execution_status_service", service)
    monkeypatch.setattr(repairs_module, "logger", logs)

    await ReconcileLoop()._cleanup_zombie_tasks(12)

    assert logs.exceptions == ["清理僵尸任务失败"]


class _NoAckQuery:
    def __init__(self, rows):
        self._rows = rows

    def only(self, *_fields):
        return self

    def limit(self, _count):
        return self

    async def all(self):
        return self._rows


class _NoAckTaskRuns:
    rows: list = []

    @classmethod
    def filter(cls, *_args, **_kwargs):
        return _NoAckQuery(cls.rows)


def _no_ack_run(run_id: str, worker_id, lease_id=None):
    return SimpleNamespace(
        id=1,
        run_id=run_id,
        worker_id=worker_id,
        lease_id=lease_id,  # P1-FN-11: 判死路径需比对 run.lease_id 与 Worker 当前代际
        dispatch_status=DispatchStatus.DISPATCHED,
        runtime_status=None,
        dispatch_updated_at=datetime.now(UTC),
    )


status_module = importlib.import_module("antcode_core.application.services.scheduler.execution_status_service")
liveness_module = importlib.import_module("antcode_master.control.dispatch_ack_liveness")


@pytest.mark.asyncio
async def test_dispatched_no_ack_skips_runs_on_alive_workers(monkeypatch):
    """P1-FN-06/11 回归:
    - 绑定 Worker + lease_id 与当前活代际匹配 → 延长观察
    - Worker 换代(lease_id 不匹配)或已死 → 判死
    - 未绑定 lease_id + Worker 有任何活代际 → 延长观察(回退到 worker 级)
    - 未绑定 Worker → 直接判死
    """
    _NoAckTaskRuns.rows = [
        _no_ack_run("run-alive-current-gen", worker_id=5, lease_id="lease-1"),
        _no_ack_run("run-old-gen", worker_id=5, lease_id="lease-0"),  # Worker 已换代
        _no_ack_run("run-dead-worker", worker_id=6, lease_id="lease-x"),
        _no_ack_run("run-unbound-alive-worker", worker_id=5, lease_id=None),
        _no_ack_run("run-unbound", worker_id=None, lease_id=None),
    ]
    from unittest.mock import AsyncMock

    monkeypatch.setattr(liveness_module, "TaskRun", _NoAckTaskRuns)
    service = _StatusService()
    monkeypatch.setattr(status_module, "execution_status_service", service)
    # P1-FN-11: alive_lease_by_worker 返回 {worker_id: current_lease_id}
    monkeypatch.setattr(
        liveness_module,
        "load_alive_lease_by_worker",
        AsyncMock(return_value={5: "lease-1"}),
    )

    await ReconcileLoop()._check_dispatched_no_ack(31)

    failed_runs = sorted(update["run_id"] for update in service.dispatch_updates)
    # run-alive-current-gen 和 run-unbound-alive-worker 被延长观察(worker 5 有活代际)
    # run-old-gen 因为绑定 lease-0 但当前是 lease-1,判死
    # run-dead-worker 因为 worker 6 无活代际,判死
    # run-unbound 因为无 worker,判死
    assert failed_runs == ["run-dead-worker", "run-old-gen", "run-unbound"]
    assert all(update["status"] == DispatchStatus.FAILED for update in service.dispatch_updates)


@pytest.mark.asyncio
async def test_dispatched_no_ack_skips_round_when_liveness_unavailable(monkeypatch):
    """P1-FN-06/11: 活性证据不可得(Redis 故障)时本轮显式跳过判死,避免误杀。"""
    from unittest.mock import AsyncMock

    _NoAckTaskRuns.rows = [_no_ack_run("run-x", worker_id=5, lease_id="lease-1")]
    monkeypatch.setattr(liveness_module, "TaskRun", _NoAckTaskRuns)
    service = _StatusService()
    monkeypatch.setattr(status_module, "execution_status_service", service)
    monkeypatch.setattr(
        liveness_module,
        "load_alive_lease_by_worker",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )

    await ReconcileLoop()._check_dispatched_no_ack(32)

    assert service.dispatch_updates == []


@pytest.mark.asyncio
async def test_reconcile_runs_all_checks_in_order():
    events = []
    loop = ReconcileLoop()

    async def record(name):
        events.append(name)

    loop._check_timeout_tasks = lambda _token: record("timeout")
    loop._check_dispatched_no_ack = lambda _token: record("dispatch")
    loop._check_inconsistent_states = lambda _token: record("inconsistent")
    loop._cleanup_zombie_tasks = lambda _token: record("zombie")

    await loop._reconcile(21)

    assert events == ["timeout", "dispatch", "inconsistent", "zombie"]
