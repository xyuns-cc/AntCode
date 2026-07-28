"""PostgreSQL keyset query for an ascending task-log history window."""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.logs.postgres_log_service import TASK_LOG_COLUMNS


async def fetch_history_window_rows(
    run_id: str,
    *,
    limit: int,
    snapshot_id: int,
    lower: int,
    after: int | None,
) -> list[dict[str, Any]]:
    from tortoise import Tortoise

    where = 'WHERE "run_id" = $1 AND "id" >= $3 AND "id" <= $2 '
    params: list[Any] = [run_id, snapshot_id, lower]
    if after is not None:
        where += 'AND "id" > $4 '
        params.append(after)
    params.append(limit)
    sql = f'SELECT {TASK_LOG_COLUMNS} FROM "task_logs" {where}ORDER BY "id" ASC LIMIT ${len(params)}'
    connection = Tortoise.get_connection("default")
    _, rows = await connection.execute_query(sql, params)
    return list(rows or [])


__all__ = ["fetch_history_window_rows"]
