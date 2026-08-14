"""Performance indexes created after Tortoise schema generation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loguru import logger

from scripts.init_db_current_schema import CURRENT_SCHEMA_INDEXES

PERFORMANCE_INDEXES: list[tuple[str, str]] = [
    (
        "idx_task_executions_crawl_batch_id",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_crawl_batch_id"
            ON public."task_executions" (("result_data"->>'crawl_batch_id'))
            WHERE "result_data"->>'crawl_batch_id' IS NOT NULL
        """,
    ),
    (
        "idx_task_executions_crawl_batch_status",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_crawl_batch_status"
            ON public."task_executions" (("result_data"->>'crawl_batch_id'), "status")
            WHERE "result_data"->>'crawl_batch_id' IS NOT NULL
        """,
    ),
    (
        "idx_crawl_batches_status_created",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_crawl_batches_status_created"
            ON public."crawl_batches" ("status", "created_at" DESC)
        """,
    ),
    (
        "idx_task_logs_event_id_unique",
        """
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_logs_event_id_unique"
            ON public."task_logs" ("event_id")
            WHERE "event_id" IS NOT NULL
        """,
    ),
    (
        "idx_task_logs_run_id_id",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_logs_run_id_id"
            ON public."task_logs" ("run_id", "id")
        """,
    ),
    (
        "idx_worker_install_keys_unacknowledged_recovery",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_worker_install_keys_unacknowledged_recovery"
            ON public."worker_install_keys" ("recovery_expires_at")
            WHERE "status" = 'used' AND "registration_acknowledged_at" IS NULL
        """,
    ),
    (
        "idx_worker_install_keys_registration_id_unique",
        """
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "idx_worker_install_keys_registration_id_unique"
            ON public."worker_install_keys" ("registration_id")
            WHERE "registration_id" IS NOT NULL
        """,
    ),
    *CURRENT_SCHEMA_INDEXES,
]

_INDEX_VALIDITY_SQL = """
SELECT index_row.indisvalid, index_row.indisready
  FROM pg_index index_row
  JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
  JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
 WHERE namespace.nspname = 'public' AND index_class.relname = $1
"""


async def _drop_invalid_index(connection: Any, name: str) -> None:
    _, rows = await connection.execute_query(_INDEX_VALIDITY_SQL, [name])
    if rows and (not rows[0]["indisvalid"] or not rows[0]["indisready"]):
        logger.warning("发现 INVALID/NOT READY 残留索引，先删除再重建: {}", name)
        await connection.execute_query(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')


async def _index_is_valid(connection: Any, name: str) -> bool:
    _, rows = await connection.execute_query(_INDEX_VALIDITY_SQL, [name])
    return bool(rows) and bool(rows[0]["indisvalid"]) and bool(rows[0]["indisready"])


async def create_performance_indexes(connection: Any, indexes: Iterable[tuple[str, str]]) -> None:
    """Create every canonical index and reject invalid catalog state."""
    for name, sql in indexes:
        await _drop_invalid_index(connection, name)
        await connection.execute_query(sql)
        if not await _index_is_valid(connection, name):
            raise RuntimeError(f"索引创建后仍为 INVALID/NOT READY（需人工排查）: {name}")
        logger.info("索引 OK: {}", name)


__all__ = ["PERFORMANCE_INDEXES", "create_performance_indexes"]
