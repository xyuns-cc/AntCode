from pathlib import Path

from antcode_core.domain.models.task_log import TaskLog

from scripts.init_db import PERFORMANCE_INDEXES


def test_task_logs_migration_creates_required_postgres_table():
    migration = Path("migrations/models/20260710_add_task_logs.sql").read_text()

    assert 'CREATE TABLE IF NOT EXISTS "task_logs"' in migration
    assert '"run_id" VARCHAR(64) NOT NULL' in migration
    assert '"content" TEXT NOT NULL' in migration
    assert '"event_id" VARCHAR(128)' in migration
    assert "idx_task_logs_event_id_unique" in migration
    assert "idx_task_logs_run_sequence" in migration


def test_task_logs_storage_order_index_is_online_and_consistent():
    index = next(item for item in TaskLog._meta.indexes if getattr(item, "name", None) == "idx_task_logs_run_id_id")
    migration = Path("migrations/models/20260717_add_task_logs_run_id_id_index.sql").read_text()
    init_sql = dict(PERFORMANCE_INDEXES)["idx_task_logs_run_id_id"]

    assert index.fields == ["run_id", "id"]
    for sql in (migration, init_sql):
        assert 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_logs_run_id_id"' in sql
        assert 'ON public."task_logs" ("run_id", "id")' in sql
    assert "BEGIN" not in migration
