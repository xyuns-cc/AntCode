from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import (
    DispatchStatus,
    RuntimeStatus,
    ScheduleType,
    TaskStatus,
    TaskType,
)
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.worker import Worker
from antcode_master.control.provisional_worker_cleanup import (
    _release_bound_run_ownerships,
    settle_expired_provisional_worker_runs,
)
from tortoise import Tortoise

AUTHORITY_TOKEN = 41
EXPECTED_SETTLED_RUNS = 2


@pytest_asyncio.fixture
async def cleanup_database(monkeypatch):
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    await SchedulerAuthority.create(
        name="master",
        fencing_token=AUTHORITY_TOKEN,
        activated_at=datetime.now(UTC),
    )

    async def fencing_token() -> int:
        return AUTHORITY_TOKEN

    monkeypatch.setattr(
        "antcode_master.control.provisional_worker_cleanup.require_fencing_token",
        fencing_token,
    )
    ownership_release = AsyncMock()
    monkeypatch.setattr(
        "antcode_master.control.provisional_worker_cleanup._release_bound_run_ownerships",
        ownership_release,
    )
    yield ownership_release
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


async def _task(name: str) -> Task:
    return await Task.create(
        name=name,
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
        status=TaskStatus.RUNNING,
        success_count=3,
    )


@pytest.mark.asyncio
async def test_settles_all_active_runs_and_synchronizes_tasks(cleanup_database) -> None:
    worker = await Worker.create(name="provisional", host="127.0.0.1", port=8001)
    first_task = await _task("provisional-running")
    second_task = await _task("provisional-pending")
    started_at = datetime.now(UTC) - timedelta(seconds=5)
    running = await TaskRun.create(
        task_id=first_task.id,
        run_id="provisional-running",
        worker_id=worker.id,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.RUNNING,
        status=TaskStatus.RUNNING,
        start_time=started_at,
        next_retry_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    pending = await TaskRun.create(
        task_id=second_task.id,
        run_id="provisional-pending",
        worker_id=worker.id,
        dispatch_status=DispatchStatus.DISPATCHING,
        status=TaskStatus.DISPATCHING,
    )

    settled = await settle_expired_provisional_worker_runs(worker.id)

    assert settled == EXPECTED_SETTLED_RUNS
    refreshed_running = await TaskRun.get(id=running.id)
    refreshed_pending = await TaskRun.get(id=pending.id)
    assert refreshed_running.runtime_status == RuntimeStatus.FAILED
    assert refreshed_running.dispatch_status == DispatchStatus.ACKED
    assert refreshed_running.next_retry_at is None
    assert refreshed_running.duration_seconds is not None
    assert refreshed_pending.runtime_status == RuntimeStatus.FAILED
    assert refreshed_pending.dispatch_status == DispatchStatus.FAILED
    assert (await Task.get(id=first_task.id)).status == TaskStatus.FAILED
    assert (await Task.get(id=second_task.id)).status == TaskStatus.FAILED
    cleanup_database.assert_awaited_once_with(worker.public_id, worker.id)


@pytest.mark.asyncio
async def test_stale_scheduler_authority_rejects_settlement(cleanup_database) -> None:
    worker = await Worker.create(name="stale-authority", host="127.0.0.1", port=8002)
    task = await _task("stale-authority-task")
    run = await TaskRun.create(
        task_id=task.id,
        run_id="stale-authority-run",
        worker_id=worker.id,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.RUNNING,
        status=TaskStatus.RUNNING,
    )
    await SchedulerAuthority.filter(name="master").update(fencing_token=AUTHORITY_TOKEN + 1)

    with pytest.raises(RuntimeError, match="scheduler authority changed"):
        await settle_expired_provisional_worker_runs(worker.id)

    assert (await TaskRun.get(id=run.id)).status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_releases_exact_postgres_owned_run_tokens(cleanup_database, monkeypatch) -> None:
    worker = await Worker.create(name="release-owner", host="127.0.0.1", port=8003)
    task = await _task("release-owner-task")
    run = await TaskRun.create(
        task_id=task.id,
        run_id="release-owner-run",
        worker_id=worker.id,
        lease_id="lease-one",
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.FAILED,
        status=TaskStatus.FAILED,
    )
    redis = AsyncMock()
    redis.mget.return_value = [f"{worker.public_id}:lease-one".encode()]
    redis.eval.return_value = 1

    async def redis_client():
        return redis

    monkeypatch.setattr("antcode_master.control.provisional_worker_cleanup.get_redis_client", redis_client)
    monkeypatch.setattr("antcode_master.control.provisional_worker_cleanup.redis_namespace", lambda: "tenant-a")

    await _release_bound_run_ownerships(worker.public_id, worker.id)

    redis.mget.assert_awaited_once_with([f"{{tenant-a}}:run:owner:{run.run_id}"])
    assert redis.eval.await_args.args[2] == f"{{tenant-a}}:run:owner:{run.run_id}"
    assert redis.eval.await_args.args[3] == f"{worker.public_id}:lease-one"


@pytest.mark.asyncio
async def test_rejects_foreign_run_ownership(cleanup_database, monkeypatch) -> None:
    worker = await Worker.create(name="foreign-owner", host="127.0.0.1", port=8004)
    task = await _task("foreign-owner-task")
    await TaskRun.create(
        task_id=task.id,
        run_id="foreign-owner-run",
        worker_id=worker.id,
        lease_id="lease-one",
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.FAILED,
        status=TaskStatus.FAILED,
    )
    redis = AsyncMock()
    redis.mget.return_value = [b"different-worker:different-lease"]

    async def redis_client():
        return redis

    monkeypatch.setattr("antcode_master.control.provisional_worker_cleanup.get_redis_client", redis_client)

    with pytest.raises(RuntimeError, match="ownership 与 PostgreSQL 不一致"):
        await _release_bound_run_ownerships(worker.public_id, worker.id)

    redis.eval.assert_not_awaited()
