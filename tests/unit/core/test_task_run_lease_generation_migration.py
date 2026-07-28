from pathlib import Path

from antcode_core.common.redis_stream_id import MAX_STREAM_ID_LENGTH
from antcode_core.domain.models import TaskRunLeaseGeneration

from scripts.init_db import REQUIRED_TABLES

MIGRATION = Path("migrations/models/20260727_add_task_run_lease_generations.sql")
DATABASE_SETUP = Path("docs/database-setup.md")


def test_generation_cutoff_column_fits_maximum_redis_stream_id() -> None:
    field = TaskRunLeaseGeneration._meta.fields_map["log_valid_through_id"]
    migration = MIGRATION.read_text(encoding="utf-8")

    assert field.max_length == MAX_STREAM_ID_LENGTH
    assert f"log_valid_through_id VARCHAR({MAX_STREAM_ID_LENGTH}) NULL" in migration


def test_generation_migration_is_atomic_and_rejects_incompatible_table() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")

    assert migration.startswith("BEGIN;")
    assert migration.rstrip().endswith("COMMIT;")
    assert "required_columns <> 8 OR invalid_columns > 0" in migration
    assert "RAISE EXCEPTION" in migration
    assert "pg_get_constraintdef" in migration
    assert "pg_get_indexdef" in migration


def test_generation_table_is_required_and_documented_for_upgrade() -> None:
    docs = DATABASE_SETUP.read_text(encoding="utf-8")

    assert "task_run_lease_generations" in REQUIRED_TABLES
    assert "20260727_add_task_run_lease_generations.sql" in docs
