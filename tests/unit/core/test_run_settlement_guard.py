from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.workers import run_settlement_guard as guard

EXPECTED_KEY_COUNT = 2
TEST_BATCH_SIZE = 2
EXPECTED_MGET_CALLS = 2


@pytest.mark.asyncio
async def test_empty_run_set_does_not_connect_to_redis(monkeypatch) -> None:
    get_redis = AsyncMock()
    monkeypatch.setattr(guard, "get_redis_client", get_redis)

    await guard.ensure_runs_settled([])

    get_redis.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_owner_rejects_delete() -> None:
    redis = AsyncMock()
    redis.mget.return_value = [None, b"worker-1:lease-1"]

    with pytest.raises(guard.RunSettlementPendingError, match="仍在结算"):
        await guard.ensure_runs_settled(["run-1", "run-2"], redis_client=redis)


@pytest.mark.asyncio
async def test_released_runs_allow_delete_and_deduplicate_ids() -> None:
    redis = AsyncMock()
    redis.mget.return_value = [None, None]

    await guard.ensure_runs_settled(["run-1", "run-1", "run-2"], redis_client=redis)

    keys = redis.mget.await_args.args[0]
    assert len(keys) == EXPECTED_KEY_COUNT
    assert keys[0].endswith(":run:owner:run-1")
    assert keys[1].endswith(":run:owner:run-2")


@pytest.mark.asyncio
async def test_redis_failure_is_exposed() -> None:
    redis = AsyncMock()
    redis.mget.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用"):
        await guard.ensure_runs_settled(["run-1"], redis_client=redis)


@pytest.mark.asyncio
async def test_large_run_set_is_checked_in_bounded_batches(monkeypatch) -> None:
    monkeypatch.setattr(guard, "OWNERSHIP_LOOKUP_BATCH_SIZE", TEST_BATCH_SIZE)
    redis = AsyncMock()
    redis.mget.side_effect = [[None, None], [None]]

    await guard.ensure_runs_settled(["run-1", "run-2", "run-3"], redis_client=redis)

    assert redis.mget.await_count == EXPECTED_MGET_CALLS


@pytest.mark.asyncio
async def test_redis_client_failure_is_wrapped(monkeypatch) -> None:
    monkeypatch.setattr(
        guard,
        "get_redis_client",
        AsyncMock(side_effect=RuntimeError("connection contains sensitive context")),
    )

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用") as exc_info:
        await guard.ensure_runs_settled(["run-1"])

    assert "sensitive" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_mget_response_is_unavailable() -> None:
    redis = AsyncMock()
    redis.mget.return_value = []

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用"):
        await guard.ensure_runs_settled(["run-1"], redis_client=redis)


def test_only_explicit_terminal_statuses_are_deletable() -> None:
    from antcode_core.domain.models.enums import TaskStatus

    assert guard.TASK_RUN_TERMINAL_STATUSES == {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.SKIPPED,
        TaskStatus.REJECTED,
    }
    assert TaskStatus.PAUSED not in guard.TASK_RUN_TERMINAL_STATUSES


@pytest.mark.asyncio
async def test_load_deletable_runs_rejects_nonterminal_status(monkeypatch) -> None:
    from antcode_core.domain.models.task_run import TaskRun

    query = MagicMock()
    query.exists = AsyncMock(return_value=True)
    query.using_db.return_value = query
    query.exclude.return_value = query
    monkeypatch.setattr(TaskRun, "filter", lambda **_kwargs: query)
    settled = AsyncMock()
    monkeypatch.setattr(guard, "ensure_runs_settled", settled)

    with pytest.raises(ValueError, match="未终态"):
        await guard.load_deletable_run_ids(object(), [1])

    settled.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_deletable_runs_checks_ownership(monkeypatch) -> None:
    from antcode_core.domain.models.task_run import TaskRun

    query = MagicMock()
    query.exists = AsyncMock(return_value=False)
    query.all = AsyncMock(return_value=[SimpleNamespace(run_id="run-1")])
    query.using_db.return_value = query
    query.exclude.return_value = query
    query.only.return_value = query
    monkeypatch.setattr(TaskRun, "filter", lambda **_kwargs: query)
    settled = AsyncMock()
    monkeypatch.setattr(guard, "ensure_runs_settled", settled)

    run_ids = await guard.load_deletable_run_ids(object(), [1])

    assert run_ids == ["run-1"]
    settled.assert_awaited_once_with(["run-1"])


def test_task_delete_checks_settlement_before_dependency_delete() -> None:
    scheduler = Path(
        "packages/antcode_core/src/antcode_core/application/services/scheduler/scheduler_service.py"
    ).read_text(encoding="utf-8")

    assert scheduler.index("load_deletable_run_ids(conn, [task.id])") < scheduler.index(
        "delete_run_dependency_rows(conn, run_ids)"
    )


def test_project_delete_checks_settlement_before_run_delete() -> None:
    cascade = Path(
        "packages/antcode_core/src/antcode_core/application/services/projects/project_cascade_delete.py"
    ).read_text(encoding="utf-8")

    assert cascade.index("ensure_runs_settled(cleanup_run_ids)") < cascade.index(
        "TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).delete()"
    )
