"""Interrupted-run pagination and run-scoped recovery context."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus
from antcode_master import task_persistence, task_recovery
from antcode_master.control import durable_schedule, recovery_execution, scheduler_loop
from antcode_master.control.execution_parameters import (
    RecoveryExecutionOptions,
    add_recovery_context,
    effective_execution_params,
)
from antcode_master.task_persistence import TaskCheckpoint, TaskPersistenceService

_FIRST_PAGE_LAST_ID = TaskPersistenceService.RECOVERY_PAGE_SIZE
_SECOND_PAGE_ID = _FIRST_PAGE_LAST_ID + 1
_PAGE_AFTER_ID = 7
_PAGE_RESULT_ID = 8
_EXPECTED_PAGE_LOADS = 2


@pytest.mark.asyncio
async def test_interrupted_scan_continues_after_fully_filtered_page(monkeypatch) -> None:
    service = TaskPersistenceService()
    first_page = [SimpleNamespace(id=index) for index in range(1, _FIRST_PAGE_LAST_ID + 1)]
    second_page = [SimpleNamespace(id=_SECOND_PAGE_ID)]
    checkpoint = SimpleNamespace(run_id=f"run-{_SECOND_PAGE_ID}")
    service._load_interrupted_page = AsyncMock(side_effect=[first_page, second_page])
    load_leases = AsyncMock(return_value={"worker-live": "lease-current"})
    monkeypatch.setattr(task_persistence, "load_active_lease_ids", load_leases)
    service._checkpoints_for_page = AsyncMock(side_effect=[[], [checkpoint]])

    recovered = await service.get_interrupted_tasks()

    assert recovered == [checkpoint]
    assert service._load_interrupted_page.await_count == _EXPECTED_PAGE_LOADS
    assert service._load_interrupted_page.await_args_list[0].kwargs["after_id"] == 0
    assert service._load_interrupted_page.await_args_list[1].kwargs["after_id"] == _FIRST_PAGE_LAST_ID
    load_leases.assert_awaited_once()


def _interrupted_execution(*, lease_id: str | None):
    return SimpleNamespace(
        id=1,
        run_id="run-1",
        task_id=9,
        worker_id=3,
        lease_id=lease_id,
        result_data=None,
        start_time=datetime.now(UTC),
    )


def test_recovery_requires_exact_run_lease_generation() -> None:
    task = SimpleNamespace(id=9, public_id="task-9")
    execution = _interrupted_execution(lease_id="lease-old")

    checkpoints = TaskPersistenceService._build_recovery_checkpoints(
        [execution],
        {task.id: task},
        worker_pub_map={execution.worker_id: "worker-3"},
        active_lease_ids={"worker-3": "lease-new"},
    )

    assert [checkpoint.run_id for checkpoint in checkpoints] == [execution.run_id]


def test_recovery_skips_only_matching_run_lease_generation() -> None:
    task = SimpleNamespace(id=9, public_id="task-9")
    execution = _interrupted_execution(lease_id="lease-current")

    checkpoints = TaskPersistenceService._build_recovery_checkpoints(
        [execution],
        {task.id: task},
        worker_pub_map={execution.worker_id: "worker-3"},
        active_lease_ids={"worker-3": "lease-current"},
    )

    assert checkpoints == []


@pytest.mark.asyncio
async def test_interrupted_page_uses_stable_id_keyset() -> None:
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit = AsyncMock(return_value=[SimpleNamespace(id=_PAGE_RESULT_ID)])
    task_run = SimpleNamespace(filter=MagicMock(return_value=query))

    page = await TaskPersistenceService._load_interrupted_page(
        task_run,
        SimpleNamespace(RUNNING="running"),
        datetime.now(UTC),
        after_id=_PAGE_AFTER_ID,
    )

    assert page[0].id == _PAGE_RESULT_ID
    task_run.filter.assert_called_once_with(status="running", id__gt=_PAGE_AFTER_ID)
    query.order_by.assert_called_once_with("id")
    query.limit.assert_awaited_once_with(TaskPersistenceService.RECOVERY_PAGE_SIZE)


@pytest.mark.asyncio
async def test_recovery_never_persists_shared_task_parameters(monkeypatch) -> None:
    task = SimpleNamespace(id=9, execution_params={"page": 3}, save=AsyncMock())
    monkeypatch.setattr(task_recovery.Task, "get_or_none", AsyncMock(return_value=task))
    trigger = AsyncMock(return_value="recovery-run")
    monkeypatch.setattr(scheduler_loop.scheduler_service, "trigger_task", trigger)
    service = task_recovery.TaskRecoveryService()
    service.persistence.save_checkpoint = AsyncMock(return_value=True)
    checkpoint = TaskCheckpoint(
        run_id="source-run",
        task_id=task.id,
        task_public_id="task-9",
        progress=0.5,
        checkpoint_data={"cursor": "abc"},
    )

    recovered = await service._recover_task(checkpoint)

    assert recovered is True
    assert task.execution_params == {"page": 3}
    task.save.assert_not_awaited()
    options = trigger.await_args.kwargs["recovery_options"]
    assert options.source_run_id == "source-run"
    assert options.params["_checkpoint"] == {"cursor": "abc"}
    assert trigger.await_args.kwargs["idempotency_key"] == "recovery:source-run"


def test_recovery_params_are_scoped_to_the_new_run() -> None:
    options = RecoveryExecutionOptions("source-run", {"_resume": True, "_progress": 0.5})
    recovery_data = add_recovery_context({}, options)

    assert effective_execution_params({"page": 3}, recovery_data) == {
        "page": 3,
        "_resume": True,
        "_progress": 0.5,
    }
    assert effective_execution_params({"page": 3}, None) == {"page": 3}


@pytest.mark.asyncio
async def test_source_transition_is_committed_with_recovery_run_claim(monkeypatch) -> None:
    source = SimpleNamespace(id=8, task_id=9, run_id="source-run", status=TaskStatus.RUNNING)
    query = MagicMock()
    query.using_db.return_value = query
    query.select_for_update.return_value = query
    query.first = AsyncMock(return_value=source)
    query.update = AsyncMock(return_value=1)
    monkeypatch.setattr(recovery_execution.TaskRun, "filter", MagicMock(return_value=query))

    locked = await recovery_execution.lock_interrupted_source(
        object(),
        source.task_id,
        RecoveryExecutionOptions("source-run", {}),
    )
    await recovery_execution.transition_interrupted_source(object(), locked)

    updates = query.update.await_args.kwargs
    assert updates["status"] == TaskStatus.FAILED
    assert updates["runtime_status"] == RuntimeStatus.FAILED
    assert updates["error_message"] == "任务中断，已重新调度"


@pytest.mark.asyncio
async def test_source_transition_conflict_preserves_domain_error(monkeypatch) -> None:
    source = SimpleNamespace(id=8, run_id="source-run")
    query = MagicMock()
    query.using_db.return_value = query
    query.update = AsyncMock(return_value=0)
    monkeypatch.setattr(recovery_execution.TaskRun, "filter", MagicMock(return_value=query))

    with pytest.raises(recovery_execution.RecoverySourceInvalidError, match="source-run"):
        await recovery_execution.transition_interrupted_source(object(), source)


@pytest.mark.asyncio
async def test_durable_recovery_rescans_interrupted_runs_each_tick(monkeypatch) -> None:
    runner = durable_schedule.DurableScheduleRunner(MagicMock())
    runner._scan = AsyncMock()
    recover_runs = AsyncMock(return_value={"recovered": 1, "failed": 0, "skipped": 0})
    monkeypatch.setattr(durable_schedule, "require_fencing_token", AsyncMock(return_value=7))
    monkeypatch.setattr(task_recovery.task_recovery_service, "recover_on_startup", recover_runs)

    await runner.recover_due()

    runner._scan.assert_awaited_once_with(7)
    recover_runs.assert_awaited_once()
