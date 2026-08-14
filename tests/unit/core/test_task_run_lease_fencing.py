"""Result commits are fenced by the ownership-bound PostgreSQL generation."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.task_result_commit import (
    ResultCommitOutcome,
    ResultCommitRequest,
    ResultMetadataRejected,
    TaskResultCommitter,
    _result_update_decision,
)
from antcode_core.application.services.task_run_service import TaskRunService, _ResultMetadata
from antcode_core.domain.models import TaskRun, Worker
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus


def _execution(*, lease_id: str = "lease-1", runtime_status=None):
    return SimpleNamespace(
        id=7,
        run_id="run-1",
        worker_id=42,
        lease_id=lease_id,
        dispatch_status=DispatchStatus.DISPATCHED,
        dispatch_updated_at=None,
        runtime_status=runtime_status,
        runtime_updated_at=None,
        status=TaskStatus.QUEUED,
        start_time=None,
        end_time=None,
        duration_seconds=None,
        result_data=None,
    )


def _request(*, lease_id: str = "lease-1", status=RuntimeStatus.SUCCESS):
    return ResultCommitRequest(
        run_id="run-1",
        worker_id="worker-1",
        lease_id=lease_id,
        runtime_status=status,
        status_at=datetime.now(UTC),
        exit_code=0,
        error_message=None,
        metadata_builder=lambda _execution: {"result_data": {"result": "ok"}},
    )


def _query(first=None, *, exists=True, updated=1):
    query = MagicMock()
    query.using_db.return_value = query
    query.select_for_update.return_value = query
    query.first = AsyncMock(return_value=first)
    query.exists = AsyncMock(return_value=exists)
    query.update = AsyncMock(return_value=updated)
    return query


@pytest.mark.asyncio
async def test_result_commit_rejects_generation_rebound_after_redis_validation(monkeypatch):
    execution = _execution(lease_id="lease-2")
    run_query = _query(execution)
    worker_query = _query(exists=True)
    transaction = AsyncMock()
    transaction.__aenter__.return_value = object()
    transaction.__aexit__.return_value = None
    monkeypatch.setattr(
        "antcode_core.application.services.task_result_commit.in_transaction",
        MagicMock(return_value=transaction),
    )

    with (
        patch.object(TaskRun, "filter", return_value=run_query),
        patch.object(Worker, "filter", return_value=worker_query),
    ):
        accepted = await TaskResultCommitter(AsyncMock(return_value=True)).commit(_request(lease_id="lease-1"))

    assert accepted is False
    run_query.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_bound_generation_commits_status_and_metadata(monkeypatch):
    execution = _execution()
    locked_query = _query(execution)
    update_query = _query(updated=1)
    worker_query = _query(exists=True)
    transaction = AsyncMock()
    transaction.__aenter__.return_value = object()
    transaction.__aexit__.return_value = None
    monkeypatch.setattr(
        "antcode_core.application.services.task_result_commit.in_transaction",
        MagicMock(return_value=transaction),
    )
    monkeypatch.setattr(
        "antcode_core.application.services.task_result_commit.execution_status_service._sync_task_status",
        AsyncMock(),
    )

    with (
        patch.object(TaskRun, "filter", side_effect=[locked_query, update_query]),
        patch.object(TaskRun, "get_or_none", AsyncMock(return_value=execution)),
        patch.object(Worker, "filter", return_value=worker_query),
    ):
        outcome = await TaskResultCommitter(AsyncMock(return_value=True)).commit_outcome(_request())

    assert outcome == ResultCommitOutcome(True, "run-1", RuntimeStatus.SUCCESS)
    updates = update_query.update.await_args.kwargs
    assert updates["runtime_status"] == RuntimeStatus.SUCCESS
    assert updates["dispatch_status"] == DispatchStatus.ACKED
    assert updates["result_data"] == {"result": "ok"}


def test_terminal_row_ignores_late_progress_and_rejects_conflicting_terminal():
    execution = _execution(runtime_status=RuntimeStatus.SUCCESS)
    progress = _result_update_decision(execution, _request(status=RuntimeStatus.RUNNING))
    conflicting = _result_update_decision(execution, _request(status=RuntimeStatus.FAILED))

    assert progress.accepted is True
    assert progress.updates == {}
    assert conflicting.accepted is False


def test_result_metadata_never_persists_lease_token() -> None:
    execution = SimpleNamespace(
        result_data={"lease_id": "old-token", "existing": "value"},
        start_time=None,
        duration_seconds=None,
    )

    updates = TaskRunService()._build_result_updates(
        execution,
        _ResultMetadata(
            started_at=None,
            finished_at=None,
            duration_ms=None,
            exit_code=None,
            error_message=None,
            output=None,
            data={"lease_id": "current-token", "result": "ok"},
        ),
    )

    assert updates["result_data"] == {"existing": "value", "result": "ok"}


def test_rejected_result_metadata_becomes_visible_failed_terminal() -> None:
    def reject_metadata(_execution):
        raise ResultMetadataRejected("最终结果超过服务端上限")

    request = replace(_request(), metadata_builder=reject_metadata)

    decision = _result_update_decision(_execution(), request)

    assert decision.accepted is True
    assert decision.updates["runtime_status"] == RuntimeStatus.FAILED
    assert decision.updates["status"] == TaskStatus.FAILED
    assert decision.updates["error_message"] == "最终结果超过服务端上限"
    assert "result_data" not in decision.updates


def test_rejected_metadata_replay_is_idempotent_after_failed_commit() -> None:
    def reject_metadata(_execution):
        raise ResultMetadataRejected("最终结果超过服务端上限")

    execution = _execution(runtime_status=RuntimeStatus.FAILED)
    request = replace(_request(), metadata_builder=reject_metadata)

    decision = _result_update_decision(execution, request)

    assert decision.accepted is True
    assert decision.runtime_status == RuntimeStatus.FAILED
    assert decision.updates == {}


@pytest.mark.asyncio
async def test_wrong_worker_rejection_does_not_build_metadata(monkeypatch):
    execution = _execution()
    locked_query = _query(execution)
    worker_query = _query(exists=False)
    transaction = AsyncMock()
    transaction.__aenter__.return_value = object()
    transaction.__aexit__.return_value = None
    metadata_builder = MagicMock(return_value={})
    request = replace(_request(), metadata_builder=metadata_builder)
    monkeypatch.setattr(
        "antcode_core.application.services.task_result_commit.in_transaction",
        MagicMock(return_value=transaction),
    )

    with (
        patch.object(TaskRun, "filter", return_value=locked_query),
        patch.object(Worker, "filter", return_value=worker_query),
    ):
        outcome = await TaskResultCommitter(AsyncMock(return_value=True)).commit_outcome(request)

    assert outcome == ResultCommitOutcome(False, "run-1", None)
    metadata_builder.assert_not_called()


@pytest.mark.asyncio
async def test_bound_old_lease_accepts_terminal_result(monkeypatch):
    execution = _execution()
    locked_query = _query(execution)
    update_query = _query(updated=1)
    worker_query = _query(exists=True)
    transaction = AsyncMock()
    transaction.__aenter__.return_value = object()
    transaction.__aexit__.return_value = None
    monkeypatch.setattr(
        "antcode_core.application.services.task_result_commit.in_transaction",
        MagicMock(return_value=transaction),
    )
    monkeypatch.setattr(
        "antcode_core.application.services.task_result_commit.execution_status_service._sync_task_status",
        AsyncMock(),
    )

    with (
        patch.object(TaskRun, "filter", side_effect=[locked_query, update_query]),
        patch.object(TaskRun, "get_or_none", AsyncMock(return_value=execution)),
        patch.object(Worker, "filter", return_value=worker_query),
    ):
        outcome = await TaskResultCommitter(AsyncMock(return_value=False)).commit_outcome(
            _request(status=RuntimeStatus.FAILED)
        )

    assert outcome == ResultCommitOutcome(True, "run-1", RuntimeStatus.FAILED)
    assert update_query.update.await_args.kwargs["runtime_status"] == RuntimeStatus.FAILED
