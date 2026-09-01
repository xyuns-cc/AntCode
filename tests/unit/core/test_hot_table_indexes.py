from unittest.mock import AsyncMock

import pytest

from scripts import init_db
from scripts.init_db import PERFORMANCE_INDEXES


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
