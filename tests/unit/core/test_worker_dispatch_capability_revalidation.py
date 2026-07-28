from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import worker_dispatcher as dispatcher_module
from antcode_core.application.services.workers.worker_capability_routing import WorkerCapabilityChangedError
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher


@pytest.mark.asyncio
async def test_dispatch_rejects_worker_whose_capabilities_change_before_publish(monkeypatch) -> None:
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(id=7, public_id="worker-7", name="Worker 7")
    dispatcher._select_worker = AsyncMock(return_value=worker)
    dispatcher._ensure_worker_connected = AsyncMock(return_value=True)
    dispatcher._bind_task_runs_to_worker = AsyncMock()
    dispatcher._send_batch_to_queue = AsyncMock()
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
    dispatcher._send_batch_to_queue.assert_not_awaited()
