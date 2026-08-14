from pathlib import Path

from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task_run import TaskRun

from scripts.init_db import REQUIRED_TABLES
from scripts.init_db_current_schema import CURRENT_SCHEMA_COLUMNS, CURRENT_SCHEMA_INDEXES

MIGRATION = Path("migrations/models/20260730_add_scheduler_authority.sql")


def test_scheduler_authority_schema_is_available_to_fresh_and_legacy_databases() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    columns = {(table, column) for table, column, _ddl in CURRENT_SCHEMA_COLUMNS}
    indexes = {name for name, _ddl in CURRENT_SCHEMA_INDEXES}

    assert SchedulerAuthority._meta.db_table == "scheduler_authority"
    assert "scheduler_authority" in REQUIRED_TABLES
    assert ("task_executions", "scheduler_fencing_token") in columns
    assert "idx_task_executions_scheduler_fencing_token" in indexes
    assert "CREATE TABLE IF NOT EXISTS scheduler_authority" in sql
    assert "ADD COLUMN IF NOT EXISTS scheduler_fencing_token BIGINT NULL" in sql
    assert "scheduler_fencing_token" in TaskRun._meta.fields_map
