from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher


@pytest.mark.asyncio
async def test_dispatch_binds_run_before_publishing(monkeypatch):
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(id=7, public_id="worker-7", name="Worker 7")
    events: list[str] = []

    dispatcher._select_worker = AsyncMock(return_value=worker)
    dispatcher._ensure_worker_connected = AsyncMock(return_value=True)

    async def bind_runs(tasks, worker_id):
        assert tasks[0]["task_id"] == "run-1"
        assert worker_id == 7
        events.append("bound")
        return 1

    async def publish(**_kwargs):
        events.append("published")
        return {
            "success": True,
            "accepted_count": 1,
            "rejected_count": 0,
        }

    monkeypatch.setattr(dispatcher, "_bind_task_runs_to_worker", bind_runs)
    monkeypatch.setattr(dispatcher, "_send_batch_to_queue", publish)

    result = await dispatcher.dispatch_batch(
        tasks=[
            {
                "task_id": "run-1",
                "project_id": "project-1",
                "project_type": "rule",
            }
        ]
    )

    assert result.success is True
    assert events == ["bound", "published"]


@pytest.mark.asyncio
async def test_bind_task_runs_updates_every_existing_run(monkeypatch):
    dispatcher = WorkerTaskDispatcher()
    query = SimpleNamespace(update=AsyncMock(return_value=2))

    from antcode_core.domain.models import TaskRun

    filter_mock = MagicMock(return_value=query)
    monkeypatch.setattr(TaskRun, "filter", filter_mock)

    updated = await dispatcher._bind_task_runs_to_worker(
        [
            {"task_id": "run-1"},
            {"run_id": "run-2", "task_id": "ignored"},
        ],
        worker_id=7,
    )

    assert updated == 2
    filter_mock.assert_called_once_with(run_id__in={"run-1", "run-2"})
    query.update.assert_awaited_once_with(worker_id=7)
