"""Migration contract for repairing JSON-text include_paths written by the old form wire format."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

_LEGACY_SCHEMA = """
    CREATE TABLE project_sources (
        id BIGINT PRIMARY KEY,
        include_paths JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        marker TEXT NOT NULL
    );
"""

PROJECT_SOURCE_INCLUDE_PATHS = MigrationCase(
    name="20260817_repair_project_source_include_paths.sql",
    # 损坏形态：整段 JSON 文本被当成列表的唯一元素落库。
    setup_sql=f"""
        {_LEGACY_SCHEMA}
        INSERT INTO project_sources
        VALUES (1, jsonb_build_array($json$["libs", "shared"]$json$), NOW(), 'preserved');
        INSERT INTO project_sources
        VALUES (2, jsonb_build_array('src'), NOW(), 'already-clean');
    """,
    seed_after_first_sql="",
    # 断言修复结果本身：脏行还原成两个元素，干净行原样不动。
    # 连跑两次后仍是这个值，即证明可重复执行且不会二次改写。
    marker_query="""
        SELECT string_agg(joined, '|' ORDER BY id)
          FROM (
                SELECT source.id,
                       (
                        SELECT string_agg(path_item, ',' ORDER BY item_order)
                          FROM jsonb_array_elements_text(source.include_paths)
                               WITH ORDINALITY AS element(path_item, item_order)
                       ) AS joined
                  FROM project_sources AS source
               ) AS flattened
    """,
    marker_value="libs,shared|src",
    schema=SchemaExpectation(
        table="project_sources",
        columns=("id", "include_paths", "updated_at", "marker"),
    ),
    failure=FailureExpectation(
        # 两个元素的数组不属于「唯一已知损坏形态」，脚本不猜；
        # 残留的 `[]` 命中守卫，必须 RAISE EXCEPTION 而不是静默留在库里。
        setup_sql=f"""
            {_LEGACY_SCHEMA}
            INSERT INTO project_sources
            VALUES (1, jsonb_build_array('[]', 'extra'), NOW(), 'preserved');
        """,
        marker_query="SELECT marker FROM project_sources WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "include_paths", "updated_at", "marker"),
    ),
)

__all__ = ["PROJECT_SOURCE_INCLUDE_PATHS"]
