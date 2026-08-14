"""Migration contract for persisted ProjectRule dispatch constraints."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

PROJECT_RULE_DISPATCH_CONSTRAINTS = MigrationCase(
    name="20260811_add_project_rule_dispatch_constraints.sql",
    setup_sql="""
        CREATE TABLE project_rules (id BIGINT PRIMARY KEY, marker TEXT NOT NULL);
        INSERT INTO project_rules VALUES (1, 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM project_rules WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="project_rules",
        columns=("region", "require_render"),
        indexes=("idx_project_rules_region",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE project_rules (
                id BIGINT PRIMARY KEY, region JSON NULL, marker TEXT NOT NULL
            );
            INSERT INTO project_rules VALUES (1, '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM project_rules WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "region", "marker"),
        absent_columns=("require_render",),
        absent_indexes=("idx_project_rules_region",),
    ),
)

__all__ = ["PROJECT_RULE_DISPATCH_CONSTRAINTS"]
