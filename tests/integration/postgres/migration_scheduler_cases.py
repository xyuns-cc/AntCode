"""Migration cases for scheduler authority epochs."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

SCHEDULER_AUTHORITY = MigrationCase(
    name="20260730_add_scheduler_authority.sql",
    setup_sql="""
        CREATE TABLE task_executions (id BIGINT PRIMARY KEY, marker TEXT NOT NULL);
        INSERT INTO task_executions VALUES (1, 'preserved');
    """,
    seed_after_first_sql="""
        INSERT INTO scheduler_authority (name, fencing_token, activated_at)
        VALUES ('master', 17, NOW())
    """,
    marker_query="SELECT fencing_token FROM scheduler_authority WHERE name = 'master'",
    marker_value=17,
    schema=SchemaExpectation(
        table="task_executions",
        columns=("scheduler_fencing_token",),
        indexes=("idx_task_executions_scheduler_fencing_token",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE task_executions (
                id BIGINT PRIMARY KEY, scheduler_fencing_token JSON, marker TEXT NOT NULL
            );
            INSERT INTO task_executions VALUES (1, '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM task_executions WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "scheduler_fencing_token", "marker"),
        absent_indexes=("idx_task_executions_scheduler_fencing_token",),
    ),
)

__all__ = ["SCHEDULER_AUTHORITY"]
