"""Migration contract for dropping the never-written projects.runtime_worker_id column."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

_LEGACY_SCHEMA = """
    CREATE TABLE projects (
        id BIGINT PRIMARY KEY,
        runtime_worker_id BIGINT,
        bound_worker_id BIGINT,
        marker TEXT NOT NULL
    );
"""

PROJECT_RUNTIME_WORKER_ID = MigrationCase(
    name="20260819_drop_project_runtime_worker_id.sql",
    setup_sql=f"""
        {_LEGACY_SCHEMA}
        INSERT INTO projects VALUES (1, NULL, 42, 'preserved');
        INSERT INTO projects VALUES (2, NULL, NULL, 'unbound');
    """,
    seed_after_first_sql="",
    # 删列后 bound_worker_id 是「运行时在哪个 Worker」的唯一真源，它必须一字不动——
    # 连跑两次仍是这个值，即证明第二遍走的是「列不存在就跳过」而不是又改了一次数据。
    marker_query=(
        "SELECT string_agg(marker || ':' || coalesce(bound_worker_id::text, 'null'), ',' ORDER BY id) FROM projects"
    ),
    marker_value="preserved:42,unbound:null",
    schema=SchemaExpectation(
        table="projects",
        columns=("id", "bound_worker_id", "marker"),
        absent_columns=("runtime_worker_id",),
    ),
    failure=FailureExpectation(
        # 有非 NULL 行就推翻了「全仓无写入方」这个删列前提，此时删列等于丢数据。
        # 守卫必须 RAISE EXCEPTION 把整条 DO 块回滚掉，让列和值一起留在库里等人核对。
        setup_sql=f"""
            {_LEGACY_SCHEMA}
            INSERT INTO projects VALUES (1, 7, NULL, 'preserved');
        """,
        # 同时断言列还在（能取到 runtime_worker_id）与值没丢（还是 7）。
        marker_query="SELECT marker || ':' || runtime_worker_id FROM projects WHERE id = 1",
        marker_value="preserved:7",
        present_columns=("id", "runtime_worker_id", "bound_worker_id", "marker"),
    ),
)

__all__ = ["PROJECT_RUNTIME_WORKER_ID"]
