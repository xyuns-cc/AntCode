from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import worker_selection
from antcode_core.application.services.workers.worker_dispatcher import WorkerLoadBalancer, WorkerTaskDispatcher
from antcode_core.domain.models.enums import WorkerStatus
from antcode_core.domain.models.worker import Worker


@pytest.mark.asyncio
async def test_explicit_worker_must_match_required_region(monkeypatch) -> None:
    worker = SimpleNamespace(
        id=7,
        public_id="worker-7",
        name="Worker 7",
        region="cn-west",
        status=WorkerStatus.ONLINE,
    )
    query = SimpleNamespace(first=AsyncMock(return_value=worker))
    monkeypatch.setattr(Worker, "filter", lambda **_filters: query)
    capability_lookup = AsyncMock()
    monkeypatch.setattr(worker_selection, "resolve_capability_map", capability_lookup)
    monkeypatch.setattr(worker_selection, "has_unacknowledged_v2_registration", AsyncMock(return_value=False))

    selected = await WorkerTaskDispatcher()._select_worker(
        worker_id="worker-7",
        region="cn-east",
    )

    assert selected is None
    capability_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_worker_rejects_unacknowledged_v2_registration(monkeypatch) -> None:
    worker = SimpleNamespace(
        id=7,
        public_id="worker-7",
        name="Worker 7",
        region="cn-east",
        status=WorkerStatus.ONLINE,
    )
    query = SimpleNamespace(first=AsyncMock(return_value=worker))
    monkeypatch.setattr(Worker, "filter", lambda **_filters: query)
    pending = AsyncMock(return_value=True)
    capability_lookup = AsyncMock()
    monkeypatch.setattr(worker_selection, "has_unacknowledged_v2_registration", pending)
    monkeypatch.setattr(worker_selection, "resolve_capability_map", capability_lookup)

    selected = await WorkerTaskDispatcher()._select_worker(worker_id="worker-7")

    assert selected is None
    pending.assert_awaited_once_with("worker-7")
    capability_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_acl_candidate_list_is_filtered_by_region(monkeypatch) -> None:
    west = SimpleNamespace(
        id=1,
        name="west",
        region="cn-west",
        status=WorkerStatus.ONLINE,
        capabilities={},
        metrics={},
    )
    east = SimpleNamespace(
        id=2,
        name="east",
        region="cn-east",
        status=WorkerStatus.ONLINE,
        capabilities={},
        metrics={},
    )
    balancer = WorkerLoadBalancer()
    metrics = {
        "cpu": 10,
        "memory": 10,
        "runningTasks": 0,
        "queuedTasks": 0,
        "maxConcurrentTasks": 10,
    }
    monkeypatch.setattr(balancer, "_refresh_resources", AsyncMock(return_value=metrics))
    ready_filter = AsyncMock(return_value=[west, east])
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.filter_registration_ready_workers",
        ready_filter,
    )

    selected = await balancer.select_best_worker(
        workers=[west, east],
        region="cn-east",
    )

    assert selected is east
    assert balancer._refresh_resources.await_args_list == [((east,),)]


@pytest.mark.asyncio
async def test_auto_selection_filters_unacknowledged_v2_registration(monkeypatch) -> None:
    pending = SimpleNamespace(id=1, name="pending", region=None, status=WorkerStatus.ONLINE)
    balancer = WorkerLoadBalancer()
    ready_filter = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.filter_registration_ready_workers",
        ready_filter,
    )
    refresh = AsyncMock()
    monkeypatch.setattr(balancer, "_refresh_resources", refresh)

    selected = await balancer.select_best_worker(workers=[pending])

    assert selected is None
    ready_filter.assert_awaited_once_with([pending])
    refresh.assert_not_awaited()
