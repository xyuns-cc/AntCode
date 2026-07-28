"""Migration contracts for the scheduler outbox table."""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

SCHEDULER_OUTBOX = MigrationCase(
    name="20260710_add_scheduler_outbox.sql",
    setup_sql="CREATE TABLE migration_sentinel (marker TEXT NOT NULL)",
    seed_after_first_sql="""
        INSERT INTO scheduler_outbox
            (public_id, event_type, aggregate_type, aggregate_id, payload, available_at)
        VALUES ('outbox-1', 'dispatch', 'task', 'task-1', '{"marker":"outbox"}', NOW())
    """,
    marker_query="SELECT payload->>'marker' FROM scheduler_outbox WHERE public_id = 'outbox-1'",
    marker_value="outbox",
    schema=SchemaExpectation(
        table="scheduler_outbox",
        columns=(
            "id",
            "public_id",
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "payload",
            "attempts",
            "available_at",
            "published_at",
            "last_error",
            "created_at",
        ),
        indexes=("idx_scheduler_outbox_pending", "idx_scheduler_outbox_aggregate"),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE scheduler_outbox (id BIGINT PRIMARY KEY, marker TEXT NOT NULL);
            INSERT INTO scheduler_outbox VALUES (1, 'preserved');
        """,
        marker_query="SELECT marker FROM scheduler_outbox WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "marker"),
        absent_indexes=("idx_scheduler_outbox_pending", "idx_scheduler_outbox_aggregate"),
    ),
)

__all__ = ["SCHEDULER_OUTBOX"]
