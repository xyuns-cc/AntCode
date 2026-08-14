"""Migration contract for database integrity alignment."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

DATABASE_INTEGRITY = MigrationCase(
    name="20260731_align_database_integrity.sql",
    setup_sql="""
        CREATE TABLE audit_logs (
            id BIGINT PRIMARY KEY, user_id INTEGER NULL, marker TEXT NOT NULL
        );
        INSERT INTO audit_logs VALUES (1, 7, 'preserved');
        CREATE TABLE redundant_public_ids (
            id BIGINT PRIMARY KEY, public_id VARCHAR(32) NOT NULL UNIQUE
        );
        CREATE INDEX idx_redundant_public_ids_public_id
            ON redundant_public_ids (public_id);
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM audit_logs WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(table="audit_logs", columns=("user_id", "marker")),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE audit_logs (
                id BIGINT PRIMARY KEY, user_id JSON NULL, marker TEXT NOT NULL
            );
            INSERT INTO audit_logs VALUES (1, '7', 'preserved');
        """,
        marker_query="SELECT marker FROM audit_logs WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "user_id", "marker"),
    ),
)

__all__ = ["DATABASE_INTEGRITY"]
