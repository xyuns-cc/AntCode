"""Online index-only PostgreSQL migration cases."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

TASK_LOG_STORAGE_INDEX = MigrationCase(
    name="20260717_add_task_logs_run_id_id_index.sql",
    setup_sql="""
        CREATE TABLE task_logs (
            id BIGSERIAL PRIMARY KEY, run_id VARCHAR(64) NOT NULL, marker TEXT NOT NULL
        );
        INSERT INTO task_logs (run_id, marker) VALUES ('run-1', 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM task_logs WHERE run_id = 'run-1'",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="task_logs",
        columns=("id", "run_id", "marker"),
        indexes=("idx_task_logs_run_id_id",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE migration_sentinel (marker TEXT NOT NULL);
            INSERT INTO migration_sentinel VALUES ('preserved');
        """,
        marker_query="SELECT marker FROM migration_sentinel",
        marker_value="preserved",
        target_exists=False,
    ),
)

__all__ = ["TASK_LOG_STORAGE_INDEX"]
