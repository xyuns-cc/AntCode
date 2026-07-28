from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.logs.task_log_run_guard import delete_run_dependency_rows

DEPENDENCY_TABLE_COUNT = 3
LARGE_RUN_COUNT = 401
EXPECTED_CHUNK_COUNT = 9
MAX_BATCH_SIZE = 200


@pytest.mark.asyncio
async def test_dependency_cleanup_error_is_not_swallowed() -> None:
    connection = AsyncMock()
    connection.execute_query.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await delete_run_dependency_rows(connection, ["run-1"])

    connection.execute_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_dependency_cleanup_deletes_all_run_scoped_tables() -> None:
    connection = AsyncMock()

    await delete_run_dependency_rows(connection, ["run-1", "run-2"])

    statements = [call.args[0] for call in connection.execute_query.await_args_list]
    assert any('DELETE FROM "task_logs"' in statement for statement in statements)
    assert any('DELETE FROM "run_source_snapshots"' in statement for statement in statements)
    assert any('DELETE FROM "task_run_lease_generations"' in statement for statement in statements)
    assert len(statements) == DEPENDENCY_TABLE_COUNT
    assert all("$1,$2" in statement for statement in statements)


@pytest.mark.asyncio
async def test_dependency_cleanup_chunks_large_run_sets() -> None:
    connection = AsyncMock()

    await delete_run_dependency_rows(connection, [f"run-{index}" for index in range(LARGE_RUN_COUNT)])

    calls = connection.execute_query.await_args_list
    assert len(calls) == EXPECTED_CHUNK_COUNT
    assert max(len(call.args[1]) for call in calls) == MAX_BATCH_SIZE
