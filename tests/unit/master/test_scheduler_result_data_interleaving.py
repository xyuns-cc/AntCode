from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import ProjectType, TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_master.control import scheduler_loop
from tortoise import Tortoise


@pytest_asyncio.fixture
async def task_run_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.task_run"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_worker_result_committed_before_dispatch_return_is_preserved(
    task_run_database,
    monkeypatch,
) -> None:
    run_id = "run-dispatch-result-interleaving"
    await TaskRun.create(
        task_id=1,
        run_id=run_id,
        status=TaskStatus.DISPATCHING,
        scheduler_fencing_token=7,
        result_data={"retry_source_run_id": "source-run"},
    )
    stale_execution = await TaskRun.get(run_id=run_id)

    async def dispatch_then_report_result(**_kwargs):
        await TaskRun.filter(run_id=run_id).update(
            result_data={
                "retry_source_run_id": "source-run",
                "output": "worker output",
                "artifacts": [{"name": "result.json"}],
                "worker_id": "worker-result-owner",
            }
        )
        return SimpleNamespace(success=True, task_id="remote-task-1")

    from antcode_core.application.services.workers import worker_task_dispatcher

    monkeypatch.setattr(worker_task_dispatcher, "dispatch_task", dispatch_then_report_result)
    monkeypatch.setattr(
        scheduler_loop.execution_status_service,
        "update_dispatch_status",
        AsyncMock(return_value=True),
    )
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()

    result = await service._execute_distributed_task(
        SimpleNamespace(
            execution_params={},
            environment_vars={},
            timeout_seconds=30,
            priority=None,
        ),
        SimpleNamespace(public_id="project-1", type=ProjectType.CODE, env_location=None),
        run_id,
        stale_execution,
        SimpleNamespace(
            public_id="worker-1",
            name="Worker 1",
            host="127.0.0.1",
            port=8001,
        ),
    )
    await service._record_dispatch_result(stale_execution, run_id, result)

    persisted = await TaskRun.get(run_id=run_id)
    assert persisted.result_data == {
        "retry_source_run_id": "source-run",
        "output": "worker output",
        "artifacts": [{"name": "result.json"}],
        "worker_id": "worker-result-owner",
        "success": True,
        "distributed": True,
        "pending": True,
        "message": "任务已分发到 Worker Worker 1",
        "worker_name": "Worker 1",
        "remote_task_id": "remote-task-1",
    }
