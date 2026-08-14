from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import init_db
from scripts.init_db import PERFORMANCE_INDEXES

LEASE_MIGRATION = Path("migrations/models/20260722_add_task_run_lease_gen.sql")
CANCEL_MIGRATION = Path("migrations/models/20260730_add_task_run_cancel_request.sql")


def _assert_index_outside_transaction(source: str, index_name: str) -> None:
    commit_position = source.index("COMMIT;")
    index_position = source.index(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}"')
    assert commit_position < index_position


def test_task_execution_hot_table_indexes_are_concurrent() -> None:
    lease_source = LEASE_MIGRATION.read_text(encoding="utf-8")
    cancel_source = CANCEL_MIGRATION.read_text(encoding="utf-8")

    _assert_index_outside_transaction(lease_source, "idx_task_executions_lease_gen")
    assert "CREATE INDEX CONCURRENTLY" in cancel_source


def test_standard_initializer_builds_lease_generation_index_online() -> None:
    indexes = dict(PERFORMANCE_INDEXES)

    assert "CREATE INDEX CONCURRENTLY" in indexes["idx_task_executions_lease_gen"]
    assert all("CONCURRENTLY" in sql for sql in indexes.values())


@pytest.mark.asyncio
async def test_initializer_drops_interrupted_index_online() -> None:
    connection = AsyncMock()
    connection.execute_query.side_effect = [(1, [{"indisvalid": False}]), (0, [])]

    await init_db._drop_invalid_index(connection, "idx_task_executions_lease_gen")

    drop_sql = connection.execute_query.await_args_list[-1].args[0]
    assert drop_sql == 'DROP INDEX CONCURRENTLY IF EXISTS "idx_task_executions_lease_gen"'


@pytest.mark.asyncio
async def test_initializer_drops_not_ready_index_online() -> None:
    connection = AsyncMock()
    connection.execute_query.side_effect = [(1, [{"indisvalid": True, "indisready": False}]), (0, [])]

    await init_db._drop_invalid_index(connection, "idx_task_executions_lease_gen")

    drop_sql = connection.execute_query.await_args_list[-1].args[0]
    assert drop_sql == 'DROP INDEX CONCURRENTLY IF EXISTS "idx_task_executions_lease_gen"'
