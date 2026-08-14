"""Adversarial PostgreSQL checks for strict database-integrity migrations."""

import asyncpg
import pytest

from .migration_support import MigrationExecutionError, apply_migration, relation_exists

LEASE_TABLE_SQL = """
CREATE TABLE task_run_lease_generations (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    worker_id BIGINT NOT NULL,
    lease_id VARCHAR(64) NOT NULL,
    lease_gen BIGINT NULL,
    log_valid_through_id VARCHAR(41) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMPTZ NULL,
    UNIQUE (run_id, lease_id)
)
"""

TASK_PROJECT_FK_CATALOG_SQL = """
-- confdeltype 是 PG 内部的 "char" 类型（单字节），asyncpg 会原样返回 bytes
-- （b'r'），与断言里的 str 恒不相等。在 SQL 里显式转 text，让断言与驱动的
-- 类型映射解耦。
SELECT source_namespace.nspname, source.relname, target_namespace.nspname,
       target.relname, constraint_row.confdeltype::text, constraint_row.convalidated,
       source_attribute.attname, target_attribute.attname
  FROM pg_constraint constraint_row
  JOIN pg_class source ON source.oid = constraint_row.conrelid
  JOIN pg_namespace source_namespace ON source_namespace.oid = source.relnamespace
  JOIN pg_class target ON target.oid = constraint_row.confrelid
  JOIN pg_namespace target_namespace ON target_namespace.oid = target.relnamespace
  JOIN pg_attribute source_attribute
    ON source_attribute.attrelid = source.oid
   AND source_attribute.attnum = constraint_row.conkey[1]
  JOIN pg_attribute target_attribute
    ON target_attribute.attrelid = target.oid
   AND target_attribute.attnum = constraint_row.confkey[1]
 WHERE constraint_row.conname = 'fk_scheduled_tasks_project_id'
"""

TASK_PROJECT_INDEX_CATALOG_SQL = """
SELECT table_class.relname, access_method.amname, index_row.indisunique,
       index_row.indisvalid, index_row.indisready, index_row.indnkeyatts,
       index_row.indnatts, index_row.indexprs IS NULL,
       index_row.indpred IS NULL, attribute.attname
  FROM pg_index index_row
  JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
  JOIN pg_class table_class ON table_class.oid = index_row.indrelid
  JOIN pg_am access_method ON access_method.oid = index_class.relam
  JOIN pg_attribute attribute
    ON attribute.attrelid = table_class.oid
   AND attribute.attnum = index_row.indkey[0]
 WHERE index_class.relname = 'idx_scheduled_tasks_project_id'
"""

PROJECT_ID_NULLABILITY_SQL = """
SELECT is_nullable
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name = 'scheduled_tasks'
   AND column_name = 'project_id'
"""


@pytest.mark.asyncio
async def test_public_id_cleanup_preserves_non_equivalent_indexes(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE audit_logs (id BIGINT PRIMARY KEY, user_id INTEGER NULL);
        CREATE TABLE cleanup_plain (
            id BIGINT PRIMARY KEY, public_id VARCHAR(32) NOT NULL UNIQUE, marker TEXT
        );
        CREATE INDEX cleanup_plain_public_id ON cleanup_plain (public_id);
        CREATE TABLE cleanup_partial (
            id BIGINT PRIMARY KEY, public_id VARCHAR(32) NOT NULL UNIQUE, marker TEXT
        );
        CREATE INDEX cleanup_partial_public_id ON cleanup_partial (public_id) WHERE marker IS NOT NULL;
        CREATE TABLE cleanup_include (
            id BIGINT PRIMARY KEY, public_id VARCHAR(32) NOT NULL UNIQUE, marker TEXT
        );
        CREATE INDEX cleanup_include_public_id ON cleanup_include (public_id) INCLUDE (marker);
        CREATE TABLE cleanup_clustered (
            id BIGINT PRIMARY KEY, public_id VARCHAR(32) NOT NULL UNIQUE, marker TEXT
        );
        CREATE INDEX cleanup_clustered_public_id ON cleanup_clustered (public_id);
        CLUSTER cleanup_clustered USING cleanup_clustered_public_id;
        """
    )

    await apply_migration(pg_connection, "20260731_align_database_integrity.sql")

    assert not await relation_exists(pg_connection, "cleanup_plain_public_id")
    for name in ("cleanup_partial_public_id", "cleanup_include_public_id", "cleanup_clustered_public_id"):
        assert await relation_exists(pg_connection, name)


@pytest.mark.asyncio
async def test_project_rule_migration_creates_exact_columns_and_index(pg_connection) -> None:
    await pg_connection.execute("CREATE TABLE project_rules (id BIGINT PRIMARY KEY)")

    await apply_migration(pg_connection, "20260811_add_project_rule_dispatch_constraints.sql")

    columns = await pg_connection.fetch(
        """
        SELECT column_name, udt_name, character_maximum_length, is_nullable, column_default
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'project_rules'
           AND column_name IN ('region', 'require_render')
         ORDER BY column_name
        """
    )
    assert [tuple(row) for row in columns] == [
        ("region", "varchar", 50, "YES", None),
        ("require_render", "bool", None, "NO", "false"),
    ]
    index_row = await pg_connection.fetchrow(
        """
        SELECT table_class.relname, index_row.indisunique, index_row.indisvalid,
               index_row.indisready, index_row.indnkeyatts, index_row.indnatts,
               attribute.attname, pg_get_expr(index_row.indpred, index_row.indrelid)
          FROM pg_index index_row
          JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
          JOIN pg_class table_class ON table_class.oid = index_row.indrelid
          JOIN pg_attribute attribute
            ON attribute.attrelid = table_class.oid AND attribute.attnum = index_row.indkey[0]
         WHERE index_class.relname = 'idx_project_rules_region'
        """
    )
    assert tuple(index_row) == ("project_rules", False, True, True, 1, 1, "region", None)


@pytest.mark.asyncio
async def test_project_rule_migration_rejects_same_name_unique_index_on_other_table(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE project_rules (id BIGINT PRIMARY KEY);
        CREATE TABLE unrelated_rules (id BIGINT PRIMARY KEY, region VARCHAR(50));
        CREATE UNIQUE INDEX idx_project_rules_region ON unrelated_rules (region);
        """
    )

    with pytest.raises(MigrationExecutionError, match="idx_project_rules_region"):
        await apply_migration(pg_connection, "20260811_add_project_rule_dispatch_constraints.sql")


@pytest.mark.asyncio
async def test_task_project_foreign_key_is_validated_public_restrict(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE projects (id BIGINT PRIMARY KEY);
        CREATE TABLE scheduled_tasks (id BIGINT PRIMARY KEY, project_id BIGINT NULL);
        INSERT INTO projects VALUES (7);
        INSERT INTO scheduled_tasks VALUES (1, 7);
        """
    )

    await apply_migration(pg_connection, "20260811_add_task_project_foreign_key.sql")

    row = await pg_connection.fetchrow(TASK_PROJECT_FK_CATALOG_SQL)
    assert tuple(row) == (
        "public",
        "scheduled_tasks",
        "public",
        "projects",
        "r",
        True,
        "project_id",
        "id",
    )
    assert await pg_connection.fetchval(PROJECT_ID_NULLABILITY_SQL) == "NO"
    index_row = await pg_connection.fetchrow(TASK_PROJECT_INDEX_CATALOG_SQL)
    assert tuple(index_row) == ("scheduled_tasks", "btree", False, True, True, 1, 1, True, True, "project_id")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pg_connection.execute("DELETE FROM projects WHERE id = 7")


@pytest.mark.asyncio
async def test_task_project_migration_rejects_historical_null_project(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE projects (id BIGINT PRIMARY KEY);
        CREATE TABLE scheduled_tasks (id BIGINT PRIMARY KEY, project_id BIGINT NULL);
        INSERT INTO scheduled_tasks VALUES (1, NULL);
        """
    )

    with pytest.raises(MigrationExecutionError, match="project_id.*NULL"):
        await apply_migration(pg_connection, "20260811_add_task_project_foreign_key.sql")

    assert await pg_connection.fetchval(PROJECT_ID_NULLABILITY_SQL) == "YES"


@pytest.mark.asyncio
async def test_task_project_migration_rejects_same_name_index_on_other_table(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE projects (id BIGINT PRIMARY KEY);
        CREATE TABLE scheduled_tasks (id BIGINT PRIMARY KEY, project_id BIGINT NULL);
        CREATE TABLE unrelated_tasks (project_id BIGINT NOT NULL);
        CREATE INDEX idx_scheduled_tasks_project_id ON unrelated_tasks (project_id);
        """
    )

    with pytest.raises(MigrationExecutionError, match="idx_scheduled_tasks_project_id"):
        await apply_migration(pg_connection, "20260811_add_task_project_foreign_key.sql")


@pytest.mark.asyncio
async def test_orphaned_task_leaves_unvalidated_fk_protecting_new_writes(pg_connection) -> None:
    await pg_connection.execute(
        """
        CREATE TABLE projects (id BIGINT PRIMARY KEY);
        CREATE TABLE scheduled_tasks (id BIGINT PRIMARY KEY, project_id BIGINT NOT NULL);
        INSERT INTO scheduled_tasks VALUES (1, 999);
        """
    )

    with pytest.raises(MigrationExecutionError):
        await apply_migration(pg_connection, "20260811_add_task_project_foreign_key.sql")

    assert await pg_connection.fetchval(
        "SELECT NOT convalidated FROM pg_constraint WHERE conname = 'fk_scheduled_tasks_project_id'"
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pg_connection.execute("INSERT INTO scheduled_tasks VALUES (2, 998)")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind",
    ["shared_sequence", "compound_default", "missing_created_default", "lagging_sequence"],
)
async def test_lease_migration_rejects_invalid_generation_contract(pg_connection, failure_kind: str) -> None:
    if failure_kind == "shared_sequence":
        table_sql = LEASE_TABLE_SQL.replace("id BIGSERIAL", "id BIGINT DEFAULT nextval('shared_id_seq')")
        await pg_connection.execute("CREATE SEQUENCE shared_id_seq")
    else:
        table_sql = LEASE_TABLE_SQL
    if failure_kind == "missing_created_default":
        table_sql = table_sql.replace(" DEFAULT CURRENT_TIMESTAMP", "")
    await pg_connection.execute(table_sql)
    if failure_kind == "compound_default":
        await pg_connection.execute(
            """
            ALTER TABLE task_run_lease_generations ALTER COLUMN id SET DEFAULT
                nextval('task_run_lease_generations_id_seq'::regclass)
                - nextval('task_run_lease_generations_id_seq'::regclass)
            """
        )
    if failure_kind == "lagging_sequence":
        await pg_connection.execute(
            "INSERT INTO task_run_lease_generations (id, run_id, worker_id, lease_id) VALUES (99, 'r', 1, 'l')"
        )

    with pytest.raises(MigrationExecutionError):
        await apply_migration(pg_connection, "20260727_add_task_run_lease_generations.sql")


@pytest.mark.asyncio
async def test_lease_migration_rejects_same_name_lookup_index_on_other_table(pg_connection) -> None:
    await pg_connection.execute(LEASE_TABLE_SQL)
    await pg_connection.execute(
        """
        CREATE TABLE unrelated_leases (run_id VARCHAR(64), worker_id BIGINT, lease_id VARCHAR(64));
        CREATE INDEX idx_task_run_lease_generation_lookup
            ON unrelated_leases (run_id, worker_id, lease_id);
        """
    )

    with pytest.raises(MigrationExecutionError, match="idx_task_run_lease_generation_lookup"):
        await apply_migration(pg_connection, "20260727_add_task_run_lease_generations.sql")
