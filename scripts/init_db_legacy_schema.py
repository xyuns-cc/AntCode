"""Idempotent column and index repairs for legacy PostgreSQL databases."""

from __future__ import annotations

from typing import Any

from loguru import logger

from scripts.init_db_current_schema import CURRENT_SCHEMA_COLUMNS

WORKERS_LEGACY_COLUMNS: list[tuple[str, str]] = [
    (
        "api_key_hash",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "api_key_hash" VARCHAR(128) NULL',
    ),
    (
        "secret_key_hash",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "secret_key_hash" VARCHAR(128) NULL',
    ),
    (
        "secret_key_encrypted",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "secret_key_encrypted" TEXT NULL',
    ),
    (
        "api_key_previous_hash",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "api_key_previous_hash" VARCHAR(128) NULL',
    ),
    (
        "api_key_previous_expires_at",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "api_key_previous_expires_at" TIMESTAMPTZ NULL',
    ),
    (
        "redis_username",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "redis_username" VARCHAR(80) NULL',
    ),
    (
        "redis_password_encrypted",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "redis_password_encrypted" TEXT NULL',
    ),
    (
        "redis_acl_revision",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "redis_acl_revision" INTEGER NOT NULL DEFAULT 0',
    ),
    (
        "redis_acl_synced_at",
        'ALTER TABLE public."workers" ADD COLUMN IF NOT EXISTS "redis_acl_synced_at" TIMESTAMPTZ NULL',
    ),
]

NEW_FEATURE_COLUMNS: list[tuple[str, str, str]] = [
    (
        "task_executions",
        "lease_id",
        'ALTER TABLE public."task_executions" ADD COLUMN IF NOT EXISTS "lease_id" VARCHAR(64) NULL',
    ),
    (
        "task_executions",
        "lease_gen",
        'ALTER TABLE public."task_executions" ADD COLUMN IF NOT EXISTS "lease_gen" BIGINT NULL',
    ),
    (
        "scheduler_outbox",
        "consumed_at",
        'ALTER TABLE public."scheduler_outbox" ADD COLUMN IF NOT EXISTS "consumed_at" TIMESTAMPTZ NULL',
    ),
    (
        "scheduler_outbox",
        "consume_owner",
        'ALTER TABLE public."scheduler_outbox" ADD COLUMN IF NOT EXISTS "consume_owner" VARCHAR(128) NULL',
    ),
    (
        "scheduler_outbox",
        "consume_attempts",
        'ALTER TABLE public."scheduler_outbox" ADD COLUMN IF NOT EXISTS "consume_attempts" INTEGER NOT NULL DEFAULT 0',
    ),
    (
        "scheduler_outbox",
        "consume_started_at",
        'ALTER TABLE public."scheduler_outbox" ADD COLUMN IF NOT EXISTS "consume_started_at" TIMESTAMPTZ NULL',
    ),
    (
        "worker_install_keys",
        "allowed_source",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "allowed_source" VARCHAR(64) NULL',
    ),
    (
        "worker_install_keys",
        "registration_id",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "registration_id" VARCHAR(32) NULL',
    ),
    (
        "worker_install_keys",
        "recovery_secret_hash",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "recovery_secret_hash" VARCHAR(64) NULL',
    ),
    (
        "worker_install_keys",
        "registration_request_hash",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "registration_request_hash" VARCHAR(64) NULL',
    ),
    (
        "worker_install_keys",
        "credential_derivation_version",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "credential_derivation_version" SMALLINT NULL',
    ),
    (
        "worker_install_keys",
        "recovery_expires_at",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "recovery_expires_at" TIMESTAMPTZ NULL',
    ),
    (
        "worker_install_keys",
        "registration_acknowledged_at",
        'ALTER TABLE public."worker_install_keys" ADD COLUMN IF NOT EXISTS "registration_acknowledged_at" TIMESTAMPTZ NULL',
    ),
    *CURRENT_SCHEMA_COLUMNS,
]

_COLUMN_EXISTS_SQL = """
SELECT 1 AS ok FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
"""


async def _column_exists(connection: Any, table: str, column: str) -> bool:
    rows = await connection.execute_query_dict(_COLUMN_EXISTS_SQL, [table, column])
    return bool(rows)


async def _add_worker_columns(connection: Any) -> int:
    added = 0
    for column, ddl in WORKERS_LEGACY_COLUMNS:
        if await _column_exists(connection, "workers", column):
            continue
        await connection.execute_query(ddl)
        added += 1
        logger.info("旧库补列: workers.{}", column)
    return added


def _warn_worker_backfill(added_columns: int) -> None:
    if not added_columns:
        return
    logger.info(
        "workers 表补了 {} 个凭证 hash 列（老库升级路径）。标准初始化将在下一步以单事务回填凭据并删除明文列。",
        added_columns,
    )


async def _relax_legacy_project_source(connection: Any) -> None:
    rows = await connection.execute_query_dict(
        _COLUMN_EXISTS_SQL.replace("1 AS ok", "is_nullable"),
        [
            "project_sources",
            "entry_point",
        ],
    )
    if not rows or rows[0]["is_nullable"] != "NO":
        return
    await connection.execute_query('ALTER TABLE public."project_sources" ALTER COLUMN "entry_point" DROP NOT NULL')
    logger.warning("旧库 project_sources.entry_point 已放开 NOT NULL（新版本不再写该列）")


async def _allow_shared_project_sources(connection: Any) -> None:
    rows = await connection.execute_query_dict(
        """
        SELECT constraint_row.conname
          FROM pg_constraint constraint_row
         WHERE constraint_row.conrelid = 'public.project_sources'::regclass
           AND constraint_row.contype = 'u'
           AND constraint_row.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = constraint_row.conrelid AND attname = 'repository_id'),
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = constraint_row.conrelid AND attname = 'subdir')
           ]::SMALLINT[]
        """
    )
    for row in rows:
        constraint = str(row["conname"])
        quoted = constraint.replace('"', '""')
        await connection.execute_query(f'ALTER TABLE public.project_sources DROP CONSTRAINT "{quoted}"')
    index_rows = await connection.execute_query_dict(
        """
        SELECT index_class.relname AS index_name
          FROM pg_index index_row
          JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
          JOIN pg_am access_method ON access_method.oid = index_class.relam
         WHERE index_row.indrelid = 'public.project_sources'::regclass
           AND index_row.indisunique
           AND index_row.indisvalid
           AND index_row.indisready
           AND access_method.amname = 'btree'
           AND index_row.indnkeyatts = 2
           AND index_row.indnatts = 2
           AND index_row.indexprs IS NULL
           AND index_row.indpred IS NULL
           -- indkey 是 int2vector：既没有 `int2vector = smallint[]` 操作符（直接
           -- 比较抛 UndefinedFunction，整条 init_db 升级链失败），朴素的
           -- indkey::int2[] 又是 0 基下标（[0:1]），与 1 基的 ARRAY[...] 恒不相等
           -- ——那会静默漏删旧唯一索引。用 string_to_array 归一成 1 基再比。
           AND string_to_array(index_row.indkey::text, ' ')::SMALLINT[] = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = index_row.indrelid AND attname = 'repository_id'),
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = index_row.indrelid AND attname = 'subdir')
           ]::SMALLINT[]
           AND NOT EXISTS (
               SELECT 1 FROM pg_constraint constraint_row
                WHERE constraint_row.conindid = index_row.indexrelid
           )
        """
    )
    for row in index_rows:
        index_name = str(row["index_name"]).replace('"', '""')
        await connection.execute_query(f'DROP INDEX CONCURRENTLY IF EXISTS public."{index_name}"')


async def _backfill_feature_columns(connection: Any) -> None:
    added = 0
    install_key_added = False
    for table, column, ddl in NEW_FEATURE_COLUMNS:
        if await _column_exists(connection, table, column):
            continue
        await connection.execute_query(ddl)
        added += 1
        install_key_added = install_key_added or table == "worker_install_keys"
        logger.info("旧库补列: {}.{}", table, column)
    if install_key_added:
        logger.warning(
            "worker_install_keys 补了来源/回收列；请执行 "
            "`uv run python scripts/migrate_worker_install_keys.py` 回填存量来源限制。"
        )
    if added:
        logger.info("旧库补业务列 {} 个", added)


async def upgrade_legacy_schema(connection: Any) -> None:
    """Bring an existing schema to the minimum shape required by current code."""
    added_worker_columns = await _add_worker_columns(connection)
    _warn_worker_backfill(added_worker_columns)
    await _relax_legacy_project_source(connection)
    await _allow_shared_project_sources(connection)
    await _backfill_feature_columns(connection)
    logger.info("旧库结构对齐检查完成")


__all__ = [
    "NEW_FEATURE_COLUMNS",
    "WORKERS_LEGACY_COLUMNS",
    "upgrade_legacy_schema",
]
