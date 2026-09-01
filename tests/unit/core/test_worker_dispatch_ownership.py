"""P1-FN-03/P1-FN-04: 派发绑定的状态 CAS 与 Worker 行锁语义。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.workers import dispatch_bind_guard as guard
from antcode_core.application.services.workers.worker_dispatch_admission import DispatchAdmission
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher
from antcode_core.domain.models import WorkerStatus
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus

from tests.unit.core.worker_dispatch_ownership_support import (
    LEASE_GEN as _LEASE_GEN,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    LEASE_ID as _LEASE_ID,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    SCOPE_TASK_ID as _SCOPE_TASK_ID,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    TWO_RUNS as _TWO_RUNS,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    WORKER_ID as _WORKER_ID,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    RunQuery as _RunQuery,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    TaskQuery as _TaskQuery,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    Transaction as _Transaction,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    WorkerQuery as _WorkerQuery,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    scope_task as _scope_task,
)
from tests.unit.core.worker_dispatch_ownership_support import (
    snapshot as _snapshot,
)


@pytest.fixture()
def _txn(monkeypatch):
    monkeypatch.setattr(guard, "in_transaction", lambda *args, **kwargs: _Transaction())


@pytest.fixture()
def online_worker_lock(monkeypatch):
    query = _WorkerQuery(SimpleNamespace(id=_WORKER_ID, public_id="worker-7", status=WorkerStatus.ONLINE))
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=query)))
    monkeypatch.setattr(guard, "has_unacknowledged_v2_registration", AsyncMock(return_value=False))
    return query


@pytest.mark.asyncio
async def test_dispatch_binds_run_before_publishing(monkeypatch):
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(
        id=_WORKER_ID,
        public_id="worker-7",
        name="Worker 7",
        transport_mode="direct",
        capabilities={"task_types": ["rule"]},
    )
    events: list[str] = []

    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.admit_dispatch_worker",
        AsyncMock(return_value=DispatchAdmission(worker=worker)),
    )

    async def bind_runs(tasks, worker_id, snapshot):
        assert tasks[0]["task_id"] == "run-1"
        assert worker_id == _WORKER_ID
        assert snapshot.lease_id == _LEASE_ID
        events.append("bound")
        return 1

    async def publish(**_kwargs):
        events.append("published")
        return {"success": True, "accepted_count": 1, "rejected_count": 0}

    monkeypatch.setattr(dispatcher, "_bind_task_runs_to_worker", bind_runs)
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.publish_ready_batch_to_worker",
        publish,
    )
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.require_worker_current_requirements",
        AsyncMock(return_value=LeaseCapabilitySnapshot("lease-7", '{"task_types":["rule"]}', 7)),
    )

    result = await dispatcher.dispatch_batch(
        tasks=[{"task_id": "run-1", "project_id": "project-1", "project_type": "rule"}]
    )

    assert result.success is True
    assert events == ["bound", "published"]


@pytest.mark.asyncio
async def test_bind_legacy_runs_uses_state_cas(monkeypatch, _txn, online_worker_lock):
    """legacy 绑定必须带状态 CAS 并锁 Worker 行。"""
    query = _RunQuery(update_result=_TWO_RUNS)

    from antcode_core.domain.models import TaskRun

    filter_mock = MagicMock(return_value=query)
    monkeypatch.setattr(TaskRun, "filter", filter_mock)

    updated = await guard.bind_task_runs_to_worker(
        [
            {"run_id": "run-1", "task_id": "task-1"},
            {"run_id": "run-2", "task_id": "task-2"},
        ],
        _WORKER_ID,
        _snapshot(),
    )

    assert updated == _TWO_RUNS
    assert online_worker_lock.locked is True
    kwargs = filter_mock.call_args.kwargs
    assert kwargs["run_id__in"] == {"run-1", "run-2"}
    assert set(kwargs["dispatch_status__in"]) == {
        DispatchStatus.PENDING,
        DispatchStatus.DISPATCHING,
        DispatchStatus.FAILED,
    }
    assert kwargs["runtime_status__isnull"] is True
    assert query.exclude_kwargs == {"status": TaskStatus.CANCELLED}
    query.update.assert_awaited_once_with(
        worker_id=_WORKER_ID,
        lease_id=_LEASE_ID,
        lease_gen=_LEASE_GEN,
    )


@pytest.mark.asyncio
async def test_bind_scoped_run_rechecks_project_owner_before_update(monkeypatch, _txn, online_worker_lock):
    task_query = _TaskQuery(exists=True)
    run_query = _RunQuery(update_result=1)

    from antcode_core.domain.models import Task, TaskRun

    task_filter = MagicMock(return_value=task_query)
    run_filter = MagicMock(return_value=run_query)
    monkeypatch.setattr(Task, "filter", task_filter)
    monkeypatch.setattr(TaskRun, "filter", run_filter)

    updated = await guard.bind_task_runs_to_worker([_scope_task()], _WORKER_ID, _snapshot())

    assert updated == 1
    task_filter.assert_called_once_with(id=_SCOPE_TASK_ID, project_id=9, user_id=3)
    kwargs = run_filter.call_args.kwargs
    assert kwargs["run_id"] == "run-17"
    assert kwargs["task_id"] == _SCOPE_TASK_ID
    assert set(kwargs["dispatch_status__in"]) == {DispatchStatus.PENDING, DispatchStatus.FAILED}
    assert kwargs["runtime_status__isnull"] is True
    run_query.update.assert_awaited_once_with(
        worker_id=_WORKER_ID,
        lease_id=_LEASE_ID,
        lease_gen=_LEASE_GEN,
    )


@pytest.mark.asyncio
async def test_bind_scoped_run_rejects_stale_owner_scope(monkeypatch, _txn, online_worker_lock):
    task_query = _TaskQuery(exists=False)

    from antcode_core.domain.models import Task, TaskRun

    monkeypatch.setattr(Task, "filter", MagicMock(return_value=task_query))
    run_filter = MagicMock()
    monkeypatch.setattr(TaskRun, "filter", run_filter)

    with pytest.raises(RuntimeError, match="作用域已失效"):
        await guard.bind_task_runs_to_worker([_scope_task()], _WORKER_ID, _snapshot())

    run_filter.assert_not_called()


@pytest.mark.asyncio
async def test_bind_scoped_run_conflicts_when_state_not_dispatchable(monkeypatch, _txn, online_worker_lock):
    """P1-FN-03 回归：状态 CAS 未命中（终态/运行中/已取消）→ 逐条显式冲突。"""
    task_query = _TaskQuery(exists=True)
    run_query = _RunQuery(update_result=0)

    from antcode_core.domain.models import Task, TaskRun

    monkeypatch.setattr(Task, "filter", MagicMock(return_value=task_query))
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=run_query))

    with pytest.raises(guard.DispatchStateConflictError, match="run-17"):
        await guard.bind_task_runs_to_worker([_scope_task()], _WORKER_ID, _snapshot())


@pytest.mark.asyncio
async def test_bind_legacy_conflicted_run_raises_instead_of_silent_skip(monkeypatch, _txn, online_worker_lock):
    """P1-FN-03 回归：legacy run 状态冲突不得按旧行为告警放行。"""
    cas_query = _RunQuery(update_result=0)
    conflict_rows = [
        SimpleNamespace(
            run_id="run-done",
            status=TaskStatus.SUCCESS,
            dispatch_status=DispatchStatus.ACKED,
            runtime_status=RuntimeStatus.SUCCESS,
            lease_gen=_LEASE_GEN,
        )
    ]
    diag_query = _RunQuery(rows=conflict_rows)

    from antcode_core.domain.models import TaskRun

    monkeypatch.setattr(TaskRun, "filter", MagicMock(side_effect=[cas_query, diag_query]))

    with pytest.raises(guard.DispatchStateConflictError, match="run-done"):
        await guard.bind_task_runs_to_worker(
            [{"task_id": "task-1", "run_id": "run-done"}],
            _WORKER_ID,
            _snapshot(),
        )


@pytest.mark.asyncio
async def test_bind_legacy_missing_run_fails_closed(monkeypatch, _txn, online_worker_lock):
    cas_query = _RunQuery(update_result=0)
    diag_query = _RunQuery(rows=[])

    from antcode_core.domain.models import TaskRun

    monkeypatch.setattr(TaskRun, "filter", MagicMock(side_effect=[cas_query, diag_query]))

    with pytest.raises(guard.DispatchStateConflictError, match="run-ghost"):
        await guard.bind_task_runs_to_worker(
            [{"task_id": "task-1", "run_id": "run-ghost"}],
            _WORKER_ID,
            _snapshot(),
        )


@pytest.mark.asyncio
async def test_bind_legacy_requires_explicit_run_id(_txn, online_worker_lock):
    with pytest.raises(guard.DispatchStateConflictError, match="缺少耐久 run_id"):
        await guard.bind_task_runs_to_worker([{"task_id": "task-1"}], _WORKER_ID, _snapshot())


@pytest.mark.asyncio
async def test_bind_aborts_when_worker_deleted(monkeypatch, _txn):
    """P1-FN-04 回归：Worker 行已被删除（删除事务先提交）→ 中止派发绑定。"""
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=_WorkerQuery(None))))

    from antcode_core.domain.models import TaskRun

    run_filter = MagicMock()
    monkeypatch.setattr(TaskRun, "filter", run_filter)

    with pytest.raises(RuntimeError, match="已被删除"):
        await guard.bind_task_runs_to_worker([{"task_id": "run-1"}], _WORKER_ID, _snapshot())

    run_filter.assert_not_called()


@pytest.mark.asyncio
async def test_bind_aborts_when_worker_not_online(monkeypatch, _txn):
    """P1-FN-04 回归：Worker 已被禁用/离线 → 中止派发绑定。"""
    offline = SimpleNamespace(id=_WORKER_ID, status=WorkerStatus.OFFLINE)
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=_WorkerQuery(offline))))

    with pytest.raises(RuntimeError, match="非在线状态"):
        await guard.bind_task_runs_to_worker([{"task_id": "run-1"}], _WORKER_ID, _snapshot())


@pytest.mark.asyncio
async def test_bind_aborts_when_v2_registration_is_unacknowledged(monkeypatch, _txn):
    online = SimpleNamespace(id=_WORKER_ID, public_id="worker-7", status=WorkerStatus.ONLINE)
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=_WorkerQuery(online))))
    pending = AsyncMock(return_value=True)
    monkeypatch.setattr(guard, "has_unacknowledged_v2_registration", pending)

    with pytest.raises(RuntimeError, match="尚未确认 V2 注册"):
        await guard.bind_task_runs_to_worker([{"task_id": "run-1"}], _WORKER_ID, _snapshot())

    pending.assert_awaited_once()
