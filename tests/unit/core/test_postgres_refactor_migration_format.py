from pathlib import Path


def test_postgres_migrations_are_sql_only():
    migration_dir = Path("migrations/models")
    assert not list(migration_dir.glob("*.py"))
    assert list(migration_dir.glob("*.sql"))


def test_task_logs_upgrade_migration_matches_current_model():
    source = Path("migrations/models/20260710_add_task_logs.sql").read_text(encoding="utf-8")

    assert 'CREATE TABLE IF NOT EXISTS "task_logs"' in source
    assert '"run_id" VARCHAR(64) NOT NULL' in source
    assert "idx_task_logs_event_id_unique" in source
