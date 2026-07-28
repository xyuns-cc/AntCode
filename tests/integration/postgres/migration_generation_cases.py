"""Migration contracts for TaskRun lease-generation persistence."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

TASK_RUN_LEASE = MigrationCase(
    name="20260713_add_task_run_lease_id.sql",
    setup_sql="""
        CREATE TABLE task_executions (
            id BIGINT PRIMARY KEY, worker_id BIGINT, status VARCHAR(32), marker TEXT NOT NULL
        );
        INSERT INTO task_executions VALUES (1, 7, 'running', 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM task_executions WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="task_executions",
        columns=("lease_id",),
        indexes=("idx_task_executions_lease_id", "idx_task_executions_worker_lease_status"),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE task_executions (
                id BIGINT PRIMARY KEY, worker_id JSON, status VARCHAR(32), marker TEXT NOT NULL
            );
            INSERT INTO task_executions VALUES (1, '7', 'running', 'preserved');
        """,
        marker_query="SELECT marker FROM task_executions WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "worker_id", "status", "marker"),
        absent_columns=("lease_id",),
        absent_indexes=("idx_task_executions_lease_id", "idx_task_executions_worker_lease_status"),
    ),
)

TASK_RUN_LEASE_GEN = MigrationCase(
    name="20260722_add_task_run_lease_gen.sql",
    setup_sql="""
        CREATE TABLE task_executions (
            id BIGINT PRIMARY KEY, worker_id BIGINT, lease_id VARCHAR(64), marker TEXT NOT NULL
        );
        INSERT INTO task_executions VALUES (1, 7, 'lease-x', 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM task_executions WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="task_executions",
        columns=("lease_gen",),
        indexes=("idx_task_executions_lease_gen",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE task_executions (
                id BIGINT PRIMARY KEY, worker_id BIGINT, lease_id VARCHAR(64),
                lease_gen JSON, marker TEXT NOT NULL
            );
            INSERT INTO task_executions VALUES (1, 7, 'lease-x', '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM task_executions WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "worker_id", "lease_id", "lease_gen", "marker"),
        absent_indexes=("idx_task_executions_lease_gen",),
    ),
)

TASK_RUN_LEASE_GENERATIONS = MigrationCase(
    name="20260727_add_task_run_lease_generations.sql",
    setup_sql="CREATE TABLE migration_sentinel (marker TEXT NOT NULL)",
    seed_after_first_sql="""
        INSERT INTO task_run_lease_generations
            (run_id, worker_id, lease_id, lease_gen, log_valid_through_id)
        VALUES ('run-1', 7, 'lease-1', 1, '10-0')
    """,
    marker_query="""
        SELECT log_valid_through_id
          FROM task_run_lease_generations
         WHERE run_id = 'run-1' AND lease_id = 'lease-1'
    """,
    marker_value="10-0",
    schema=SchemaExpectation(
        table="task_run_lease_generations",
        columns=(
            "id",
            "run_id",
            "worker_id",
            "lease_id",
            "lease_gen",
            "log_valid_through_id",
            "created_at",
            "closed_at",
        ),
        indexes=("uq_task_run_lease_generation", "idx_task_run_lease_generation_lookup"),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE task_run_lease_generations (
                id BIGINT PRIMARY KEY,
                run_id VARCHAR(64) NOT NULL,
                worker_id BIGINT NOT NULL,
                lease_id VARCHAR(64) NOT NULL,
                lease_gen BIGINT NULL,
                log_valid_through_id VARCHAR(8) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMPTZ NULL,
                marker TEXT NOT NULL
            );
            INSERT INTO task_run_lease_generations
                (id, run_id, worker_id, lease_id, marker)
            VALUES (1, 'run-1', 7, 'lease-1', 'preserved');
        """,
        marker_query="SELECT marker FROM task_run_lease_generations WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "run_id", "worker_id", "lease_id", "marker"),
        absent_indexes=("uq_task_run_lease_generation", "idx_task_run_lease_generation_lookup"),
    ),
)

__all__ = ["TASK_RUN_LEASE", "TASK_RUN_LEASE_GEN", "TASK_RUN_LEASE_GENERATIONS"]
