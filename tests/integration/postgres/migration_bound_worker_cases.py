"""Migration contract for backfilling projects.bound_worker_id on multi-Worker clusters."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

_LEGACY_SCHEMA = """
    CREATE TABLE workers (
        id BIGINT PRIMARY KEY,
        public_id VARCHAR(32) NOT NULL UNIQUE
    );
    CREATE TABLE projects (
        id BIGINT PRIMARY KEY,
        worker_id VARCHAR(32),
        bound_worker_id BIGINT,
        execution_strategy VARCHAR(20) NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        marker TEXT NOT NULL
    );
"""

PROJECT_BOUND_WORKER = MigrationCase(
    name="20260818_backfill_project_bound_worker.sql",
    setup_sql=f"""
        {_LEGACY_SCHEMA}
        INSERT INTO workers VALUES (42, 'worker-alpha');
        INSERT INTO projects
        VALUES (1, 'worker-alpha', NULL, 'prefer', NOW(), 'preserved');
        INSERT INTO projects
        VALUES (2, 'worker-alpha', NULL, 'auto', NOW(), 'auto-stays-unbound');
    """,
    seed_after_first_sql="",
    # prefer 项目补成运行时所在节点；auto 项目的 NULL 是显式「不要绑定」，必须原样保留。
    # 连跑两次后仍是这个值，即证明第二遍回填 0 行。
    marker_query="SELECT string_agg(coalesce(bound_worker_id::text, 'null'), ',' ORDER BY id) FROM projects",
    marker_value="42,null",
    schema=SchemaExpectation(
        table="projects",
        columns=("id", "worker_id", "bound_worker_id", "execution_strategy", "updated_at", "marker"),
    ),
    failure=FailureExpectation(
        # worker_id 指向已不存在的 Worker：运行时环境不可达，守卫必须报出来。
        setup_sql=f"""
            {_LEGACY_SCHEMA}
            INSERT INTO projects
            VALUES (1, 'worker-ghost', NULL, 'prefer', NOW(), 'preserved');
        """,
        marker_query="SELECT marker FROM projects WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "worker_id", "bound_worker_id", "execution_strategy", "marker"),
    ),
)

__all__ = ["PROJECT_BOUND_WORKER"]
