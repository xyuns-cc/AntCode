from pathlib import Path


def test_task_logs_migration_creates_required_postgres_table():
    migration = Path("migrations/models/20260710_add_task_logs.sql").read_text()

    assert 'CREATE TABLE IF NOT EXISTS "task_logs"' in migration
    assert '"run_id" VARCHAR(64) NOT NULL' in migration
    assert '"content" TEXT NOT NULL' in migration
    assert '"event_id" VARCHAR(128)' in migration
    assert "idx_task_logs_event_id_unique" in migration
    assert "idx_task_logs_run_sequence" in migration
