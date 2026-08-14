import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from antcode_core.application.services.lease_service import Lease
from antcode_core.domain.models import TaskRun, Worker
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus

master_main = importlib.import_module("antcode_master.control.worker_eviction")
AUTHORITY_TOKEN = 17


@pytest.fixture(autouse=True)
def _fenced_eviction_dependencies(monkeypatch):
    monkeypatch.setattr(
        master_main,
        "require_fencing_token",
        AsyncMock(return_value=AUTHORITY_TOKEN),
    )
    monkeypatch.setattr(
        master_main,
        "execute_with_scheduler_authority",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        master_main,
        "settle_runtime_failure_snapshot",
        AsyncMock(return_value=True),
    )


def _make_task_query(tasks):
    """构造可链式 filter/only/all 的 TaskRun 查询 mock。"""
    for index, task in enumerate(tasks, start=1):
        task.id = index
        task.scheduler_fencing_token = 11
        task.dispatch_status = DispatchStatus.ACKED
        task.runtime_status = RuntimeStatus.RUNNING
        task.worker_id = 1
    task_query = MagicMock()
    task_query.filter.return_value = task_query
    task_query.only.return_value = task_query
    task_query.all = AsyncMock(return_value=tasks)
    return task_query


@pytest.mark.asyncio
async def test_late_l1_eviction_cannot_affect_worker_after_l2_grant(monkeypatch):
    current = Lease(
        worker_id="worker-1",
        lease_id="lease-l2",
        expires_at_ms=3000,
        granted_at_ms=2000,
    )
    lease_store = SimpleNamespace(get=AsyncMock(return_value=current))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task_query = _make_task_query([])
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=task_query))

    await master_main.on_worker_evicted("worker-1", "lease-l1")

    lease_store.get.assert_awaited_once_with("worker-1", include_expired=False)
    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_l2_granted_while_eviction_is_preparing_blocks_all_writes(monkeypatch):
    current = Lease(
        worker_id="worker-1",
        lease_id="lease-l2",
        expires_at_ms=3000,
        granted_at_ms=2000,
    )
    lease_store = SimpleNamespace(get=AsyncMock(return_value=current))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task_query = _make_task_query([])
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=task_query))

    await master_main.on_worker_evicted("worker-1", "lease-l1")

    assert lease_store.get.await_args_list == [call("worker-1", include_expired=False)]
    assert worker.status == "online"
    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_superseded_eviction_only_selects_evicted_generation_runs(monkeypatch):
    """worker 已换新代际时，NULL lease 的 run 不可回收，仅回收旧代际。"""
    current = Lease(
        worker_id="worker-1",
        lease_id="lease-l2",
        expires_at_ms=3000,
        granted_at_ms=2000,
    )
    lease_store = SimpleNamespace(get=AsyncMock(return_value=current))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task = SimpleNamespace(run_id="run-l1", lease_id="lease-l1")
    task_query = _make_task_query([task])
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=task_query))

    await master_main.on_worker_evicted("worker-1", "lease-l1")

    assert task_query.filter.call_args.kwargs == {"lease_id": "lease-l1"}
    settlement = master_main.settle_runtime_failure_snapshot
    assert settlement.await_args.args[:2] == (task, AUTHORITY_TOKEN)
    master_main.execute_with_scheduler_authority.assert_not_awaited()


@pytest.mark.asyncio
async def test_l1_eviction_selects_l1_and_null_lease_task_runs(monkeypatch):
    """M4 回归：lease_id 懒绑定导致的 NULL-lease RUNNING run 也要回收。"""
    from tortoise.expressions import Q

    lease_store = SimpleNamespace(get=AsyncMock(return_value=None))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task_l1 = SimpleNamespace(run_id="run-l1", lease_id="lease-l1")
    task_null = SimpleNamespace(run_id="run-null", lease_id=None)
    task_query = _make_task_query([task_l1, task_null])
    task_filter = MagicMock(return_value=task_query)
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", task_filter)

    await master_main.on_worker_evicted("worker-1", "lease-l1")

    # 首层 filter 不做 lease 过滤，lease 条件在二层 Q 里
    assert "lease_id" not in task_filter.call_args.kwargs
    lease_q = task_query.filter.call_args.args[0]
    assert isinstance(lease_q, Q)
    child_filters = [child.filters for child in lease_q.children]
    assert {"lease_id": "lease-l1"} in child_filters
    assert {"lease_id__isnull": True} in child_filters
    # 逐 run 传完整快照；统一 settlement 对 NULL lease 生成 IS NULL CAS。
    settlement = master_main.settle_runtime_failure_snapshot
    expected = {call_.args[0].run_id: call_.args[0].lease_id for call_ in settlement.await_args_list}
    assert expected == {"run-l1": "lease-l1", "run-null": None}
    master_main.execute_with_scheduler_authority.assert_awaited_once()


@pytest.mark.asyncio
async def test_l2_regranted_before_failover_skips_null_lease_runs(monkeypatch):
    """V1 竞态防护：首次 superseded 判定停在 L2 grant 之前（worker 看似失联，
    NULL-lease run 被纳入回收集），但失效前的二次复核发现 worker 已以新代际 L2
    回归 —— 此时只回收明确绑定 L1 的 run，NULL-lease run 必须跳过（可能仍存活）。"""
    l2 = Lease(
        worker_id="worker-1",
        lease_id="lease-l2",
        expires_at_ms=9000,
        granted_at_ms=8000,
    )
    # 首次 _eviction_was_superseded：L1 已过期、L2 尚未 grant → None（非 superseded）；
    # 失效前复核：worker 已在 L2 下回归 → 返回有效 L2 → 跳过 NULL-lease run。
    lease_store = SimpleNamespace(get=AsyncMock(side_effect=[None, l2]))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task_l1 = SimpleNamespace(run_id="run-l1", lease_id="lease-l1")
    task_null = SimpleNamespace(run_id="run-null", lease_id=None)
    task_query = _make_task_query([task_l1, task_null])
    task_filter = MagicMock(return_value=task_query)
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", task_filter)

    await master_main.on_worker_evicted("worker-1", "lease-l1")

    # 只有绑定 L1 的 run 被回收；NULL-lease run 因 worker 已在 L2 回归被跳过。
    settlement = master_main.settle_runtime_failure_snapshot
    evicted = {c.args[0].run_id for c in settlement.await_args_list}
    assert evicted == {"run-l1"}
    assert settlement.await_count == 1
    # worker 实际存活（持有 L2），不得被下线。
    assert worker.status == "online"
    master_main.execute_with_scheduler_authority.assert_not_awaited()
    # 复核确实又读了一次 lease store（贴近 UPDATE 的二次 get）。
    assert lease_store.get.await_count == 2


@pytest.mark.asyncio
async def test_eviction_without_generation_still_evicts_dead_worker(monkeypatch):
    """M1 回归：lease Hash 已被 PEXPIRE（evicted_lease_id 为空）时仍必须
    下线 worker 并回收其全部 RUNNING run，不能抛错放弃。"""
    lease_store = SimpleNamespace(get=AsyncMock(return_value=None))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task_bound = SimpleNamespace(run_id="run-old", lease_id="lease-old")
    task_null = SimpleNamespace(run_id="run-null", lease_id=None)
    task_query = _make_task_query([task_bound, task_null])
    task_filter = MagicMock(return_value=task_query)
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", task_filter)

    await master_main.on_worker_evicted("worker-1", "")

    # 代际未知且无有效 lease：不做 lease 过滤，全部 RUNNING run 回收
    assert "lease_id" not in task_filter.call_args.kwargs
    task_query.filter.assert_not_called()
    master_main.execute_with_scheduler_authority.assert_awaited_once()
    assert master_main.settle_runtime_failure_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_eviction_without_generation_skips_when_current_lease_valid(monkeypatch):
    """代际未知但 worker 已持有有效 lease：无法安全 fencing，整体跳过。"""
    current = Lease(
        worker_id="worker-1",
        lease_id="lease-new",
        expires_at_ms=9000,
        granted_at_ms=8000,
    )
    lease_store = SimpleNamespace(get=AsyncMock(return_value=current))
    worker = SimpleNamespace(id=1, status="online", save=AsyncMock())
    worker_query = SimpleNamespace(first=AsyncMock(return_value=worker))
    task_filter = MagicMock()
    monkeypatch.setattr(master_main, "_lease_store", lease_store)
    monkeypatch.setattr(Worker, "filter", MagicMock(return_value=worker_query))
    monkeypatch.setattr(TaskRun, "filter", task_filter)

    await master_main.on_worker_evicted("worker-1", "")

    task_filter.assert_not_called()
    master_main.execute_with_scheduler_authority.assert_not_awaited()
    master_main.settle_runtime_failure_snapshot.assert_not_awaited()
