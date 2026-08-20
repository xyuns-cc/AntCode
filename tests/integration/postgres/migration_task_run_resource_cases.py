"""Migration contract for dropping the never-written task_executions resource columns."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

# 位置化 INSERT 让建表本身自校验：真少建了一列，setup 阶段就会报 arity 不符，
# 而不是让 absent_columns 因为"列根本没存在过"平白变绿。
_LEGACY_SCHEMA = """
    CREATE TABLE task_executions (
        id BIGINT PRIMARY KEY,
        cpu_usage DOUBLE PRECISION,
        memory_usage BIGINT,
        exit_code INTEGER,
        marker TEXT NOT NULL
    );
"""

TASK_RUN_RESOURCE_USAGE = MigrationCase(
    name="20260820_drop_task_run_resource_usage.sql",
    setup_sql=f"""
        {_LEGACY_SCHEMA}
        INSERT INTO task_executions VALUES (1, NULL, NULL, 0, 'preserved');
        INSERT INTO task_executions VALUES (2, NULL, NULL, NULL, 'running');
    """,
    seed_after_first_sql="",
    # DROP COLUMN 只该动这两列，同表邻居必须一字不动——连跑两次仍是这个值，
    # 即证明第二遍走的是"列不存在就跳过"而不是又改了一次数据。
    marker_query=(
        "SELECT string_agg(marker || ':' || coalesce(exit_code::text, 'null'), ',' ORDER BY id) FROM task_executions"
    ),
    marker_value="preserved:0,running:null",
    schema=SchemaExpectation(
        table="task_executions",
        columns=("id", "exit_code", "marker"),
        absent_columns=("cpu_usage", "memory_usage"),
    ),
    failure=FailureExpectation(
        # 只给 memory_usage 造值：守卫要等循环删掉 cpu_usage 之后才撞上它。
        # 断言 cpu_usage 也还在，才证明 RAISE EXCEPTION 回滚的是整条 DO 语句，
        # 而不是只拦下当前这一列、把前一列悄悄删掉了。
        setup_sql=f"""
            {_LEGACY_SCHEMA}
            INSERT INTO task_executions VALUES (1, NULL, 4096, 7, 'preserved');
        """,
        # 同时断言列还在（能取到 memory_usage）与值没丢（还是 4096）。
        marker_query="SELECT marker || ':' || memory_usage FROM task_executions WHERE id = 1",
        marker_value="preserved:4096",
        present_columns=("id", "cpu_usage", "memory_usage", "exit_code", "marker"),
    ),
)

__all__ = ["TASK_RUN_RESOURCE_USAGE"]
