import pytest
import pytest_asyncio
from antcode_core.application.services.workers.run_ownership_service import (
    bind_worker_run_lease_generation,
)
from antcode_core.domain.models import TaskRun, TaskRunLeaseGeneration, Worker
from antcode_core.domain.models.enums import TaskStatus
from tortoise import Tortoise

CURRENT_GENERATION = 3


@pytest_asyncio.fixture
async def generation_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.worker",
                "antcode_core.domain.models.task_run",
                "antcode_core.domain.models.task_run_lease_generation",
            ]
        },
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_three_generation_transitions_preserve_each_backlog_cutoff(generation_database) -> None:
    worker = await Worker.create(name="worker-1", host="127.0.0.1")
    run = await TaskRun.create(task_id=1, run_id="run-1", worker_id=worker.id, status=TaskStatus.RUNNING)

    await _bind(worker, "lease-1", generation=1, cutoff="0-0")
    await _bind(worker, "lease-2", generation=2, cutoff="10-0")
    await _bind(worker, "lease-3", generation=CURRENT_GENERATION, cutoff="20-0")

    await run.refresh_from_db()
    history = await TaskRunLeaseGeneration.filter(run_id="run-1").order_by("lease_gen")
    assert run.lease_id == "lease-3"
    assert run.lease_gen == CURRENT_GENERATION
    assert [(row.lease_id, row.log_valid_through_id) for row in history] == [
        ("lease-1", "10-0"),
        ("lease-2", "20-0"),
        ("lease-3", None),
    ]

    with pytest.raises(PermissionError, match="不允许复用"):
        await _bind(worker, "lease-3", generation=CURRENT_GENERATION + 1, cutoff="30-0")


async def _bind(worker: Worker, lease_id: str, *, generation: int, cutoff: str) -> None:
    await bind_worker_run_lease_generation(
        worker,
        "run-1",
        lease_id=lease_id,
        lease_gen=generation,
        log_cutoff_id=cutoff,
    )
