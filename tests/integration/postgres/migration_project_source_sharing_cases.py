"""Migration contract for sharing one repository subdirectory across projects."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

PROJECT_SOURCE_SHARING = MigrationCase(
    name="20260812_allow_shared_project_sources.sql",
    setup_sql="""
        CREATE TABLE project_sources (
            id BIGINT PRIMARY KEY,
            repository_id BIGINT NOT NULL,
            subdir VARCHAR(500) NOT NULL,
            marker TEXT NOT NULL,
            UNIQUE (repository_id, subdir)
        );
        INSERT INTO project_sources VALUES (1, 7, 'src', 'preserved');
    """,
    seed_after_first_sql="""
        INSERT INTO project_sources VALUES (2, 7, 'src', 'shared');
    """,
    marker_query="SELECT marker FROM project_sources WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="project_sources",
        columns=("repository_id", "subdir"),
        indexes=("idx_project_sources_repository_subdir",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE project_sources (
                id BIGINT PRIMARY KEY,
                repository_id BIGINT NOT NULL,
                subdir VARCHAR(500) NOT NULL,
                marker TEXT NOT NULL
            );
            INSERT INTO project_sources VALUES (1, 7, 'src', 'preserved');
            CREATE UNIQUE INDEX idx_project_sources_repository_subdir
                ON project_sources (repository_id, subdir)
                WHERE repository_id > 0;
        """,
        marker_query="SELECT marker FROM project_sources WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "repository_id", "subdir", "marker"),
    ),
)

__all__ = ["PROJECT_SOURCE_SHARING"]
