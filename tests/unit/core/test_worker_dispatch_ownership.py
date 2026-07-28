"""P1-FN-03/P1-FN-04: 派发绑定的状态 CAS 与 Worker 行锁语义。"""

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.workers import dispatch_bind_guard as guard
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher
from antcode_core.domain.models import WorkerStatus
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus

_WORKER_ID = 7
_SCOPE_TASK_ID = 17
_TWO_RUNS = 2


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _WorkerQuery:
    """Worker.filter(...).using_db(conn).select_for_update().first() 的 fake。"""

    def __init__(self, worker):
        self._worker = worker
        self.locked = False

    def using_db(self, _conn):
        return self

    def select_for_update(self):
        self.locked = True
        return self

    async def first(self):
        return self._worker


class _RunQuery:
    """TaskRun 查询链 fake：filter → exclude → using_db → update/only/all。"""

    def __init__(self, *, update_result=0, rows=None):
        self.update = AsyncMock(return_value=update_result)
        self._rows = rows or []
        self.exclude_kwargs: dict | None = None

    def exclude(self, **kwargs):
        self.exclude_kwargs = kwargs
        return self

    def using_db(self, _conn):
        return self

    def only(self, *_fields):
        return self

    async def all(self):
        return self._rows


class _TaskQuery:
    def __init__(self, exists=True):
        self.exists = AsyncMock(return_value=exists)

    def using_db(self, _conn):
        return self


@pytest.fixture()
def _txn(monkeypatch):
    monkeypatch.setattr(guard, "in_transaction", lambda *args, **kwargs: _Transaction())


@pytest.fixture()
def online_worker_lock(monkeypatch):
    query = _WorkerQuery(SimpleNamespace(id=_WORKER_ID, status=WorkerStatus.ONLINE))
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=query)))
    return query


def _scope_task() -> dict:
    return {
        "task_id": _SCOPE_TASK_ID,
        "run_id": "run-17",
        "_dispatch_scope": {
            "run_id": "run-17",
            "task_id": _SCOPE_TASK_ID,
            "project_id": 9,
            "owner_id": 3,
        },
    }


@pytest.mark.asyncio
async def test_dispatch_binds_run_before_publishing(monkeypatch):
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(id=_WORKER_ID, public_id="worker-7", name="Worker 7")
    events: list[str] = []

    dispatcher._select_worker = AsyncMock(return_value=worker)
    dispatcher._ensure_worker_connected = AsyncMock(return_value=True)

    async def bind_runs(tasks, worker_id):
        assert tasks[0]["task_id"] == "run-1"
        assert worker_id == _WORKER_ID
        events.append("bound")
        return 1

    async def publish(**_kwargs):
        events.append("published")
        return {"success": True, "accepted_count": 1, "rejected_count": 0}

    monkeypatch.setattr(dispatcher, "_bind_task_runs_to_worker", bind_runs)
    monkeypatch.setattr(dispatcher, "_send_batch_to_queue", publish)

    result = await dispatcher.dispatch_batch(
        tasks=[{"task_id": "run-1", "project_id": "project-1", "project_type": "rule"}]
    )

    assert result.success is True
    assert events == ["bound", "published"]


@pytest.mark.asyncio
async def test_bind_legacy_runs_uses_state_cas(monkeypatch, _txn, online_worker_lock):
    """legacy 绑定必须带可派发状态谓词（P1-FN-03 CAS），并锁 Worker 行（P1-FN-04）。"""
    query = _RunQuery(update_result=_TWO_RUNS)

    from antcode_core.domain.models import TaskRun

    filter_mock = MagicMock(return_value=query)
    monkeypatch.setattr(TaskRun, "filter", filter_mock)

    updated = await guard.bind_task_runs_to_worker(
        [{"task_id": "run-1"}, {"run_id": "run-2", "task_id": "ignored"}],
        _WORKER_ID,
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
    query.update.assert_awaited_once_with(worker_id=_WORKER_ID)


@pytest.mark.asyncio
async def test_bind_scoped_run_rechecks_project_owner_before_update(monkeypatch, _txn, online_worker_lock):
    task_query = _TaskQuery(exists=True)
    run_query = _RunQuery(update_result=1)

    from antcode_core.domain.models import Task, TaskRun

    task_filter = MagicMock(return_value=task_query)
    run_filter = MagicMock(return_value=run_query)
    monkeypatch.setattr(Task, "filter", task_filter)
    monkeypatch.setattr(TaskRun, "filter", run_filter)

    updated = await guard.bind_task_runs_to_worker([_scope_task()], _WORKER_ID)

    assert updated == 1
    task_filter.assert_called_once_with(id=_SCOPE_TASK_ID, project_id=9, user_id=3)
    kwargs = run_filter.call_args.kwargs
    assert kwargs["run_id"] == "run-17"
    assert kwargs["task_id"] == _SCOPE_TASK_ID
    # P1-FN-03: scoped 只允许 PENDING（首次）/FAILED（显式重派）
    assert set(kwargs["dispatch_status__in"]) == {DispatchStatus.PENDING, DispatchStatus.FAILED}
    assert kwargs["runtime_status__isnull"] is True
    run_query.update.assert_awaited_once_with(worker_id=_WORKER_ID)


@pytest.mark.asyncio
async def test_bind_scoped_run_rejects_stale_owner_scope(monkeypatch, _txn, online_worker_lock):
    task_query = _TaskQuery(exists=False)

    from antcode_core.domain.models import Task, TaskRun

    monkeypatch.setattr(Task, "filter", MagicMock(return_value=task_query))
    run_filter = MagicMock()
    monkeypatch.setattr(TaskRun, "filter", run_filter)

    with pytest.raises(RuntimeError, match="作用域已失效"):
        await guard.bind_task_runs_to_worker([_scope_task()], _WORKER_ID)

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
        await guard.bind_task_runs_to_worker([_scope_task()], _WORKER_ID)


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
        )
    ]
    diag_query = _RunQuery(rows=conflict_rows)

    from antcode_core.domain.models import TaskRun

    monkeypatch.setattr(TaskRun, "filter", MagicMock(side_effect=[cas_query, diag_query]))

    with pytest.raises(guard.DispatchStateConflictError, match="run-done"):
        await guard.bind_task_runs_to_worker([{"task_id": "run-done"}], _WORKER_ID)


@pytest.mark.asyncio
async def test_bind_legacy_missing_run_keeps_compat_warning(monkeypatch, _txn, online_worker_lock):
    """无 TaskRun 记录的兼容调用保持旧行为：告警放行，不判冲突。"""
    cas_query = _RunQuery(update_result=0)
    diag_query = _RunQuery(rows=[])

    from antcode_core.domain.models import TaskRun

    monkeypatch.setattr(TaskRun, "filter", MagicMock(side_effect=[cas_query, diag_query]))

    updated = await guard.bind_task_runs_to_worker([{"task_id": "run-ghost"}], _WORKER_ID)

    assert updated == 0


@pytest.mark.asyncio
async def test_bind_aborts_when_worker_deleted(monkeypatch, _txn):
    """P1-FN-04 回归：Worker 行已被删除（删除事务先提交）→ 中止派发绑定。"""
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=_WorkerQuery(None))))

    from antcode_core.domain.models import TaskRun

    run_filter = MagicMock()
    monkeypatch.setattr(TaskRun, "filter", run_filter)

    with pytest.raises(RuntimeError, match="已被删除"):
        await guard.bind_task_runs_to_worker([{"task_id": "run-1"}], _WORKER_ID)

    run_filter.assert_not_called()


@pytest.mark.asyncio
async def test_bind_aborts_when_worker_not_online(monkeypatch, _txn):
    """P1-FN-04 回归：Worker 已被禁用/离线 → 中止派发绑定。"""
    offline = SimpleNamespace(id=_WORKER_ID, status=WorkerStatus.OFFLINE)
    monkeypatch.setattr(guard, "Worker", SimpleNamespace(filter=MagicMock(return_value=_WorkerQuery(offline))))

    with pytest.raises(RuntimeError, match="非在线状态"):
        await guard.bind_task_runs_to_worker([{"task_id": "run-1"}], _WORKER_ID)
