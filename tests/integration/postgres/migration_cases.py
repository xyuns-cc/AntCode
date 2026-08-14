"""Executable legacy-schema cases for every hand-written PostgreSQL migration."""

from .migration_cancel_cases import TASK_RUN_CANCEL_REQUEST
from .migration_generation_cases import TASK_RUN_LEASE, TASK_RUN_LEASE_GEN, TASK_RUN_LEASE_GENERATIONS
from .migration_index_cases import TASK_LOG_STORAGE_INDEX
from .migration_integrity_cases import DATABASE_INTEGRITY
from .migration_outbox_cases import SCHEDULER_OUTBOX
from .migration_project_rule_cases import PROJECT_RULE_DISPATCH_CONSTRAINTS
from .migration_project_source_sharing_cases import PROJECT_SOURCE_SHARING
from .migration_scheduler_cases import SCHEDULER_AUTHORITY
from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation
from .migration_task_project_fk_cases import TASK_PROJECT_FOREIGN_KEY
from .migration_worker_credential_cases import WORKER_CREDENTIALS

TASK_LOGS = MigrationCase(
    name="20260710_add_task_logs.sql",
    setup_sql="CREATE TABLE migration_sentinel (marker TEXT NOT NULL)",
    seed_after_first_sql="""
        INSERT INTO task_logs (event_id, run_id, content, timestamp)
        VALUES ('event-1', 'run-1', 'preserved log', NOW())
    """,
    marker_query="SELECT content FROM task_logs WHERE event_id = 'event-1'",
    marker_value="preserved log",
    schema=SchemaExpectation(
        table="task_logs",
        columns=(
            "id",
            "event_id",
            "run_id",
            "log_type",
            "content",
            "sequence",
            "timestamp",
            "level",
            "source",
            "created_at",
        ),
        indexes=(
            "idx_task_logs_run_sequence",
            "idx_task_logs_run_type",
            "idx_task_logs_timestamp",
            "idx_task_logs_event_id_unique",
        ),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE task_logs (id BIGINT PRIMARY KEY, run_id VARCHAR(64), marker TEXT NOT NULL);
            INSERT INTO task_logs VALUES (1, 'run-1', 'preserved');
        """,
        marker_query="SELECT marker FROM task_logs WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "run_id", "marker"),
        absent_indexes=(
            "idx_task_logs_run_sequence",
            "idx_task_logs_run_type",
            "idx_task_logs_timestamp",
            "idx_task_logs_event_id_unique",
        ),
    ),
)

USER_SESSIONS = MigrationCase(
    name="20260710_add_user_sessions.sql",
    setup_sql="CREATE TABLE migration_sentinel (marker TEXT NOT NULL)",
    seed_after_first_sql="""
        INSERT INTO user_sessions (user_id, jti, expires_at, device_info)
        VALUES (7, 'session-1', NOW() + INTERVAL '1 hour', 'preserved device')
    """,
    marker_query="SELECT device_info FROM user_sessions WHERE jti = 'session-1'",
    marker_value="preserved device",
    schema=SchemaExpectation(
        table="user_sessions",
        columns=("id", "user_id", "jti", "created_at", "expires_at", "revoked_at", "device_info"),
        indexes=(
            "idx_user_sessions_user_id",
            "idx_user_sessions_expires_at",
            "idx_user_sessions_user_revoked",
        ),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE user_sessions (id BIGINT PRIMARY KEY, user_id BIGINT, marker TEXT NOT NULL);
            INSERT INTO user_sessions VALUES (1, 7, 'preserved');
        """,
        marker_query="SELECT marker FROM user_sessions WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "user_id", "marker"),
        absent_indexes=(
            "idx_user_sessions_user_id",
            "idx_user_sessions_expires_at",
            "idx_user_sessions_user_revoked",
        ),
    ),
)

PROJECT_SOURCE_MIRRORS = MigrationCase(
    name="20260711_remove_project_source_mirrors.sql",
    setup_sql="""
        CREATE TABLE project_sources (
            id BIGINT PRIMARY KEY, entry_point TEXT, runtime_config JSONB, marker TEXT NOT NULL
        );
        INSERT INTO project_sources VALUES (1, 'main.py', '{}', 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM project_sources WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="project_sources",
        columns=("id", "marker"),
        absent_columns=("entry_point", "runtime_config"),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE project_sources (
                id BIGINT PRIMARY KEY, entry_point TEXT, runtime_config JSONB, marker TEXT NOT NULL
            );
            INSERT INTO project_sources VALUES (1, 'main.py', '{}', 'preserved');
            CREATE VIEW project_runtime_configs AS SELECT runtime_config FROM project_sources;
        """,
        marker_query="SELECT marker FROM project_sources WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "entry_point", "runtime_config", "marker"),
    ),
)

OUTBOX_CONSUMPTION = MigrationCase(
    name="20260713_add_scheduler_outbox_consumption.sql",
    setup_sql="""
        CREATE TABLE scheduler_outbox (id BIGINT PRIMARY KEY, marker TEXT NOT NULL);
        INSERT INTO scheduler_outbox VALUES (1, 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM scheduler_outbox WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="scheduler_outbox",
        columns=("consumed_at", "consume_owner", "consume_started_at"),
        indexes=("idx_scheduler_outbox_consumption",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE scheduler_outbox (id BIGINT PRIMARY KEY, consumed_at JSON, marker TEXT NOT NULL);
            INSERT INTO scheduler_outbox VALUES (1, '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM scheduler_outbox WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "consumed_at", "marker"),
        absent_columns=("consume_owner", "consume_started_at"),
        absent_indexes=("idx_scheduler_outbox_consumption",),
    ),
)

INSTALL_KEY_SOURCE = MigrationCase(
    name="20260713_add_worker_install_key_allowed_source.sql",
    setup_sql="""
        CREATE TABLE worker_install_keys (id BIGINT PRIMARY KEY, marker TEXT NOT NULL);
        INSERT INTO worker_install_keys VALUES (1, 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM worker_install_keys WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="worker_install_keys",
        columns=("allowed_source",),
        column_comment=(
            "allowed_source",
            "Authoritative Worker install-key registration source restriction (IP/CIDR only)",
        ),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE migration_sentinel (marker TEXT NOT NULL);
            INSERT INTO migration_sentinel VALUES ('preserved');
        """,
        marker_query="SELECT marker FROM migration_sentinel",
        marker_value="preserved",
        target_exists=False,
    ),
)

INSTALL_KEY_RECOVERY = MigrationCase(
    name="20260717_add_worker_registration_recovery.sql",
    setup_sql="""
        CREATE TABLE worker_install_keys (id BIGINT PRIMARY KEY, status VARCHAR(20), marker TEXT NOT NULL);
        INSERT INTO worker_install_keys VALUES (1, 'pending', 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM worker_install_keys WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="worker_install_keys",
        columns=(
            "registration_id",
            "recovery_secret_hash",
            "registration_request_hash",
            "credential_derivation_version",
            "recovery_expires_at",
            "registration_acknowledged_at",
        ),
        indexes=(
            "idx_worker_install_keys_registration_id_unique",
            "idx_worker_install_keys_unacknowledged_recovery",
        ),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE migration_sentinel (marker TEXT NOT NULL);
            INSERT INTO migration_sentinel VALUES ('preserved');
        """,
        marker_query="SELECT marker FROM migration_sentinel",
        marker_value="preserved",
        target_exists=False,
    ),
)

OUTBOX_CONSUME_ATTEMPTS = MigrationCase(
    name="20260720_add_scheduler_outbox_consume_attempts.sql",
    setup_sql="""
        CREATE TABLE scheduler_outbox (id BIGINT PRIMARY KEY, marker TEXT NOT NULL);
        INSERT INTO scheduler_outbox VALUES (1, 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM scheduler_outbox WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="scheduler_outbox",
        columns=("consume_attempts",),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE scheduler_outbox (id BIGINT PRIMARY KEY, consume_attempts JSON, marker TEXT NOT NULL);
            INSERT INTO scheduler_outbox VALUES (1, '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM scheduler_outbox WHERE id = 1",
        marker_value="preserved",
        present_columns=("id", "consume_attempts", "marker"),
    ),
)

MIGRATION_CASES = (
    SCHEDULER_OUTBOX,
    TASK_LOGS,
    USER_SESSIONS,
    WORKER_CREDENTIALS,
    PROJECT_SOURCE_MIRRORS,
    OUTBOX_CONSUMPTION,
    TASK_RUN_LEASE,
    INSTALL_KEY_SOURCE,
    TASK_LOG_STORAGE_INDEX,
    INSTALL_KEY_RECOVERY,
    OUTBOX_CONSUME_ATTEMPTS,
    TASK_RUN_LEASE_GEN,
    TASK_RUN_LEASE_GENERATIONS,
    SCHEDULER_AUTHORITY,
    TASK_RUN_CANCEL_REQUEST,
    DATABASE_INTEGRITY,
    PROJECT_RULE_DISPATCH_CONSTRAINTS,
    TASK_PROJECT_FOREIGN_KEY,
    PROJECT_SOURCE_SHARING,
)
