"""Migration contract for the Task -> Project database foreign key."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

TASK_PROJECT_FOREIGN_KEY = MigrationCase(
    name="20260811_add_task_project_foreign_key.sql",
    setup_sql="""
        CREATE TABLE projects (id BIGINT PRIMARY KEY);
        CREATE TABLE scheduled_tasks (
            id BIGINT PRIMARY KEY, project_id BIGINT NULL, marker TEXT NOT NULL
        );
        INSERT INTO projects VALUES (7);
        INSERT INTO scheduled_tasks VALUES (1, 7, 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM scheduled_tasks WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="scheduled_tasks",
        columns=("project_id", "marker"),
        indexes=("idx_scheduled_tasks_project_id",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE projects (id BIGINT PRIMARY KEY);
            CREATE TABLE scheduled_tasks (
                id BIGINT PRIMARY KEY, project_id BIGINT NOT NULL, marker TEXT NOT NULL
            );
            INSERT INTO scheduled_tasks VALUES (1, 999, 'preserved');
        """,
        marker_query="SELECT marker FROM scheduled_tasks WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "project_id", "marker"),
    ),
)

__all__ = ["TASK_PROJECT_FOREIGN_KEY"]
