from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.workers import worker_dispatcher as dispatcher_module
from antcode_core.application.services.workers.worker_capability_routing import WorkerCapabilityChangedError
from antcode_core.application.services.workers.worker_dispatch_admission import DispatchAdmission
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher


def _admit(monkeypatch, worker) -> None:
    monkeypatch.setattr(
        dispatcher_module,
        "admit_dispatch_worker",
        AsyncMock(return_value=DispatchAdmission(worker=worker)),
    )


@pytest.mark.asyncio
async def test_dispatch_rejects_worker_whose_capabilities_change_before_publish(monkeypatch) -> None:
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(id=7, public_id="worker-7", name="Worker 7")
    _admit(monkeypatch, worker)
    dispatcher._bind_task_runs_to_worker = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(dispatcher_module, "publish_ready_batch_to_worker", publish)
    monkeypatch.setattr(
        dispatcher_module,
        "require_worker_current_requirements",
        AsyncMock(side_effect=WorkerCapabilityChangedError("Worker 能力或 Lease 在入队前已变化")),
    )

    result = await dispatcher.dispatch_batch(
        tasks=[{"task_id": "run-1", "project_id": "project-1", "project_type": "rule"}]
    )

    assert result.success is False
    assert result.error == "Worker 能力或 Lease 在入队前已变化"
    dispatcher._bind_task_runs_to_worker.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_passes_revalidated_lease_generation_to_publish(monkeypatch) -> None:
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(id=7, public_id="worker-7", name="Worker 7")
    snapshot = LeaseCapabilitySnapshot("lease-7", '{"task_types":["rule"]}', 7)
    _admit(monkeypatch, worker)
    dispatcher._bind_task_runs_to_worker = AsyncMock(return_value=1)
    publish = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(dispatcher_module, "publish_ready_batch_to_worker", publish)
    monkeypatch.setattr(
        dispatcher_module,
        "require_worker_current_requirements",
        AsyncMock(return_value=snapshot),
    )

    result = await dispatcher.dispatch_batch(
        tasks=[{"task_id": "run-1", "project_id": "project-1", "project_type": "rule"}]
    )

    assert result.success is True
    assert publish.await_args.kwargs["lease_snapshot"] == snapshot
