"""Migration contract for durable TaskRun cancellation requests."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

TASK_RUN_CANCEL_REQUEST = MigrationCase(
    name="20260730_add_task_run_cancel_request.sql",
    setup_sql="""
        CREATE TABLE task_executions (
            id BIGINT PRIMARY KEY, status VARCHAR(32), marker TEXT NOT NULL
        );
        INSERT INTO task_executions VALUES (1, 'running', 'preserved');
    """,
    seed_after_first_sql="""
        UPDATE task_executions
           SET cancel_requested_at = NOW(), cancel_requested_by = 7
         WHERE id = 1
    """,
    marker_query="SELECT marker FROM task_executions WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="task_executions",
        columns=("cancel_requested_at", "cancel_requested_by"),
        indexes=("idx_task_executions_cancel_requested_at",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE task_executions (
                id BIGINT PRIMARY KEY,
                cancel_requested_at JSON,
                marker TEXT NOT NULL
            );
            INSERT INTO task_executions VALUES (1, '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM task_executions WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "cancel_requested_at", "marker"),
        absent_columns=("cancel_requested_by",),
        absent_indexes=("idx_task_executions_cancel_requested_at",),
    ),
)

__all__ = ["TASK_RUN_CANCEL_REQUEST"]
