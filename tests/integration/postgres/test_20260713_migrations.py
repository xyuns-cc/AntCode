from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

from .migration_cases import MIGRATION_CASES
from .migration_support import (
    REPO_ROOT,
    AsyncpgTortoiseConnection,
    MetadataRedis,
    MigrationCase,
    MigrationExecutionError,
    SchemaExpectation,
    apply_migration,
    apply_migrations,
    column_comment,
    column_names,
    index_names,
    relation_exists,
    require_test_database_url,
)

CONFIGURED_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
EXPECTED_INSTALL_KEY_MIGRATION_COUNT = 2
EXPECTED_REDUNDANT_PUBLIC_ID_UNIQUE_INDEX_COUNT = 2

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

init_db_script = importlib.import_module("scripts.init_db")
_migrate_rows = importlib.import_module("scripts.migrate_worker_install_keys")._migrate_rows


def test_database_url_guard_requires_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="显式设置 TEST_DATABASE_URL"):
        require_test_database_url()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pass@127.0.0.1:5432/antcode",
        "postgresql://user:pass@127.0.0.1:5432/postgres",
        "sqlite:///antcode_migration_test",
    ],
)
def test_database_url_guard_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(ValueError):
        require_test_database_url(url)


def test_configured_database_url_passes_guard_or_fails_loudly() -> None:
    if CONFIGURED_TEST_DATABASE_URL:
        assert require_test_database_url(CONFIGURED_TEST_DATABASE_URL) == CONFIGURED_TEST_DATABASE_URL
        return
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        require_test_database_url(CONFIGURED_TEST_DATABASE_URL)


# 清单完整性判据本身不碰数据库，已搬到 tests/unit/core/test_postgres_refactor_migration_format.py，
# 那里才在 release-gate 的 `make test` 范围内。


@pytest.mark.asyncio
async def test_fresh_database_init_creates_current_schema(pg_connection, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", CONFIGURED_TEST_DATABASE_URL)
    from antcode_core.infrastructure.db import tortoise as tortoise_module

    tortoise_module._cached_config.cache_clear()
    await init_db_script._generate_schemas()
    await init_db_script._upgrade_legacy_schema()
    await init_db_script._align_database_integrity()
    await init_db_script._create_performance_indexes()
    await init_db_script._validate_schema_contracts()

    assert {"consumed_at", "consume_owner", "consume_started_at"}.issubset(
        await column_names(pg_connection, "scheduler_outbox")
    )
    assert "lease_id" in await column_names(pg_connection, "task_executions")
    assert "scheduler_fencing_token" in await column_names(pg_connection, "task_executions")
    assert "idx_task_executions_scheduler_fencing_token" in await index_names(pg_connection, "task_executions")
    assert await relation_exists(pg_connection, "scheduler_authority")
    assert "allowed_source" in await column_names(pg_connection, "worker_install_keys")
    assert await pg_connection.fetchval(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'fk_scheduled_tasks_project_id'"
    )

    await pg_connection.execute("CREATE TABLE migration_test_sentinel (value TEXT NOT NULL)")
    await pg_connection.execute("INSERT INTO migration_test_sentinel VALUES ('preserved')")
    await apply_migrations(pg_connection)
    await apply_migrations(pg_connection)
    assert await pg_connection.fetchval("SELECT value FROM migration_test_sentinel") == "preserved"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MIGRATION_CASES, ids=lambda case: case.name)
async def test_legacy_schema_upgrade_is_idempotent_and_preserves_data(pg_connection, case: MigrationCase) -> None:
    await pg_connection.execute(case.setup_sql)
    await apply_migration(pg_connection, case.name)
    if case.seed_after_first_sql:
        await pg_connection.execute(case.seed_after_first_sql)
    await _assert_schema(pg_connection, case.schema)

    await apply_migration(pg_connection, case.name)

    await _assert_schema(pg_connection, case.schema)
    assert await pg_connection.fetchval(case.marker_query) == case.marker_value


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MIGRATION_CASES, ids=lambda case: case.name)
async def test_failed_sql_migration_rolls_back_schema_and_preserves_data(pg_connection, case: MigrationCase) -> None:
    await pg_connection.execute(case.failure.setup_sql)

    with pytest.raises(MigrationExecutionError):
        await apply_migration(pg_connection, case.name)

    assert await pg_connection.fetchval(case.failure.marker_query) == case.failure.marker_value
    await _assert_failure_state(pg_connection, case)


@pytest.mark.asyncio
async def test_database_integrity_migration_widens_user_id_and_drops_only_redundant_index(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE audit_logs (id BIGINT PRIMARY KEY, user_id INTEGER NULL);
        CREATE TABLE redundant_public_ids (
            id BIGINT PRIMARY KEY, public_id VARCHAR(32) NOT NULL UNIQUE
        );
        CREATE INDEX idx_redundant_public_ids_public_id
            ON redundant_public_ids (public_id);
        """
    )

    await apply_migration(pg_connection, "20260731_align_database_integrity.sql")

    user_id_type = await pg_connection.fetchval(
        """
        SELECT udt_name FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'user_id'
        """
    )
    assert user_id_type == "int8"
    assert not await relation_exists(pg_connection, "idx_redundant_public_ids_public_id")
    assert (
        await pg_connection.fetchval(
            "SELECT COUNT(*) FROM pg_index WHERE indrelid = 'public.redundant_public_ids'::regclass AND indisunique"
        )
        == EXPECTED_REDUNDANT_PUBLIC_ID_UNIQUE_INDEX_COUNT
    )


@pytest.mark.asyncio
async def test_install_key_data_migration_rolls_back_then_retries_idempotently(pg_connection) -> None:
    await _create_install_key_table(pg_connection)
    await apply_migration(pg_connection, "20260713_add_worker_install_key_allowed_source.sql")
    now = datetime.now(UTC)
    await pg_connection.executemany(
        "INSERT INTO worker_install_keys (id, key, status, expires_at) VALUES ($1, $2, $3, $4)",
        [
            (1, "EXPIRED-PLAINTEXT", "pending", now - timedelta(minutes=1)),
            (2, "ACTIVE-PLAINTEXT", "pending", now + timedelta(hours=1)),
        ],
    )
    redis = MetadataRedis({})
    adapter = AsyncpgTortoiseConnection(pg_connection)

    with pytest.raises(RuntimeError, match="缺少 Redis 来源元数据"):
        async with pg_connection.transaction():
            await _migrate_rows(adapter, redis, "antcode")
    rows = await pg_connection.fetch("SELECT key, status, allowed_source FROM worker_install_keys ORDER BY id")
    assert [tuple(row) for row in rows] == [
        ("EXPIRED-PLAINTEXT", "pending", None),
        ("ACTIVE-PLAINTEXT", "pending", None),
    ]

    redis.values["antcode:worker:install-key:meta:ACTIVE-PLAINTEXT"] = '{"allowed_source":"10.2.3.4/24"}'
    async with pg_connection.transaction():
        assert await _migrate_rows(adapter, redis, "antcode") == EXPECTED_INSTALL_KEY_MIGRATION_COUNT
    async with pg_connection.transaction():
        assert await _migrate_rows(adapter, redis, "antcode") == 0
    rows = await pg_connection.fetch("SELECT key, status, allowed_source FROM worker_install_keys ORDER BY id")
    assert rows[0]["status"] == "expired"
    assert rows[1]["key"] != "ACTIVE-PLAINTEXT"
    assert rows[1]["allowed_source"] == "10.2.3.0/24"


async def _assert_schema(connection, expected: SchemaExpectation) -> None:
    actual_columns = await column_names(connection, expected.table)
    actual_indexes = await index_names(connection, expected.table)
    assert set(expected.columns).issubset(actual_columns)
    assert not set(expected.absent_columns).intersection(actual_columns)
    assert set(expected.indexes).issubset(actual_indexes)
    if expected.column_comment:
        column, comment = expected.column_comment
        assert await column_comment(connection, expected.table, column) == comment


async def _assert_failure_state(connection, case: MigrationCase) -> None:
    failure = case.failure
    assert await relation_exists(connection, case.schema.table) is failure.target_exists
    if not failure.target_exists:
        return
    actual_columns = await column_names(connection, case.schema.table)
    actual_indexes = await index_names(connection, case.schema.table)
    assert set(failure.present_columns).issubset(actual_columns)
    assert not set(failure.absent_columns).intersection(actual_columns)
    assert not set(failure.absent_indexes).intersection(actual_indexes)


async def _create_install_key_table(connection) -> None:
    await connection.execute(
        "CREATE TABLE worker_install_keys ("
        "id BIGINT PRIMARY KEY, key VARCHAR(64) NOT NULL UNIQUE, "
        "status VARCHAR(20) NOT NULL, expires_at TIMESTAMPTZ NOT NULL)"
    )
