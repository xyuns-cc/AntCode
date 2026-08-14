"""TaskRun result consumer status and timing contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.result_status_contract import (
    ResultStatusContractError,
    validate_result_timing,
)
from antcode_core.application.services.task_result_commit import ResultCommitOutcome
from antcode_core.application.services.task_run_service import TaskRunService, _ResultMetadata
from antcode_core.domain.models.enums import RuntimeStatus

_STARTED = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
_FINISHED = _STARTED + timedelta(seconds=1)
_STATUS_CASES = (
    ("pending", RuntimeStatus.QUEUED, None),
    ("running", RuntimeStatus.RUNNING, None),
    ("completed", RuntimeStatus.SUCCESS, _FINISHED),
    ("failed", RuntimeStatus.FAILED, _FINISHED),
    ("cancelled", RuntimeStatus.CANCELLED, _FINISHED),
    ("timeout", RuntimeStatus.TIMEOUT, _FINISHED),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected", "finished_at"), _STATUS_CASES)
async def test_consumer_accepts_every_proto_status(status, expected, finished_at):
    service = TaskRunService(AsyncMock(return_value=True))
    outcome = ResultCommitOutcome(True, "run-1", expected)
    commit = AsyncMock(return_value=outcome)

    with patch(
        "antcode_core.application.services.task_run_service.TaskResultCommitter.commit_outcome",
        commit,
    ):
        assert (
            await service.update_result_outcome(
                "run-1",
                status,
                started_at=_STARTED,
                finished_at=finished_at,
                duration_ms=1_000 if finished_at else None,
                data={"lease_id": "lease-1"},
                worker_id="worker-1",
            )
            == outcome
        )

    assert commit.await_args.args[0].runtime_status is expected


@pytest.mark.parametrize("status", (RuntimeStatus.QUEUED, RuntimeStatus.RUNNING))
@pytest.mark.parametrize(
    ("finished_at", "duration_ms"),
    ((_FINISHED, None), (None, 1)),
)
def test_progress_timing_rejects_terminal_fields(status, finished_at, duration_ms):
    with pytest.raises(ResultStatusContractError):
        validate_result_timing(
            status,
            started_at=_STARTED,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )


@pytest.mark.parametrize(
    ("started_at", "finished_at", "duration_ms"),
    (
        (_STARTED, None, None),
        (_FINISHED, _STARTED, 0),
        (_STARTED, _FINISHED, -1),
        (_STARTED, _FINISHED, "not-a-number"),
        (_STARTED, _FINISHED, float("nan")),
        ("not-a-time", _FINISHED, 1_000),
    ),
)
def test_terminal_timing_rejects_invalid_values(started_at, finished_at, duration_ms):
    with pytest.raises(ResultStatusContractError):
        validate_result_timing(
            RuntimeStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )


@pytest.mark.asyncio
async def test_invalid_timing_never_reaches_result_committer():
    service = TaskRunService(AsyncMock(return_value=True))
    commit = AsyncMock()

    with patch(
        "antcode_core.application.services.task_run_service.TaskResultCommitter.commit_outcome",
        commit,
    ):
        outcome = await service.update_result_outcome(
            "run-1",
            "running",
            finished_at=_FINISHED,
            data={"lease_id": "lease-1"},
            worker_id="worker-1",
        )

    assert outcome == ResultCommitOutcome(False, "run-1", None)
    commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("reported_duration_ms", "expected_seconds"),
    ((0, 1.0), (1_000, 1.0)),
)
def test_persistence_derives_duration_from_authoritative_timestamps(
    reported_duration_ms,
    expected_seconds,
):
    execution = type(
        "Execution",
        (),
        {
            "start_time": _STARTED,
            "end_time": None,
            "duration_seconds": None,
        },
    )()

    assert (
        TaskRunService._result_duration_seconds(
            execution,
            start_dt=None,
            finish_dt=_FINISHED,
            duration_ms=reported_duration_ms,
        )
        == expected_seconds
    )


def test_terminal_replay_does_not_replace_first_persisted_timing():
    execution = SimpleNamespace(
        start_time=_STARTED,
        end_time=_FINISHED,
        duration_seconds=1.0,
        result_data={},
    )

    updates = TaskRunService()._build_result_updates(
        execution,
        _ResultMetadata(
            started_at=_STARTED,
            finished_at=_FINISHED + timedelta(seconds=1),
            duration_ms=2_000,
            exit_code=None,
            error_message=None,
            output=None,
            data=None,
        ),
    )

    assert "start_time" not in updates
    assert "end_time" not in updates
    assert "duration_seconds" not in updates
