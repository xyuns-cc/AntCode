"""Declarative PostgreSQL contracts for columns and indexes used at runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class ColumnContract:
    table: str
    name: str
    udt_name: str
    max_length: int | None = None
    nullable: bool = True
    default: str | None = None
    check_default: bool = True


@dataclass(frozen=True, kw_only=True)
class IndexContract:
    name: str
    table: str
    keys: tuple[str, ...]
    unique: bool = False
    predicate: str | None = None
    access_method: str = "btree"


@dataclass(frozen=True, kw_only=True)
class GeneratedPrimaryKeyContract:
    table: str
    columns: tuple[str, ...]
    generated_column: str


@dataclass(frozen=True, kw_only=True)
class UniqueColumnsContract:
    table: str
    columns: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ForeignKeyContract:
    name: str
    table: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    delete_action: str
    referenced_schema: str = "public"


def _nullable(table: str, name: str, udt_name: str, *, max_length: int | None = None) -> ColumnContract:
    return ColumnContract(table=table, name=name, udt_name=udt_name, max_length=max_length)


def _required(
    table: str,
    name: str,
    udt_name: str,
    *,
    max_length: int | None = None,
    default: str | None = None,
    check_default: bool = True,
) -> ColumnContract:
    return ColumnContract(
        table=table,
        name=name,
        udt_name=udt_name,
        max_length=max_length,
        nullable=False,
        default=default,
        check_default=check_default,
    )


COLUMN_CONTRACTS = (
    _nullable("workers", "api_key_hash", "varchar", max_length=128),
    _nullable("workers", "secret_key_hash", "varchar", max_length=128),
    _nullable("workers", "secret_key_encrypted", "text"),
    _nullable("workers", "api_key_previous_hash", "varchar", max_length=128),
    _nullable("workers", "api_key_previous_expires_at", "timestamptz"),
    _nullable("workers", "redis_username", "varchar", max_length=80),
    _nullable("workers", "redis_password_encrypted", "text"),
    _required("workers", "redis_acl_revision", "int4", default="0"),
    _nullable("workers", "redis_acl_synced_at", "timestamptz"),
    _nullable("task_executions", "lease_id", "varchar", max_length=64),
    _nullable("task_executions", "lease_gen", "int8"),
    _nullable("task_executions", "cancel_requested_at", "timestamptz"),
    _nullable("task_executions", "cancel_requested_by", "int8"),
    _nullable("task_executions", "scheduler_fencing_token", "int8"),
    _nullable("scheduler_outbox", "consumed_at", "timestamptz"),
    _nullable("scheduler_outbox", "consume_owner", "varchar", max_length=128),
    _required("scheduler_outbox", "consume_attempts", "int4", default="0"),
    _nullable("scheduler_outbox", "consume_started_at", "timestamptz"),
    _nullable("worker_install_keys", "allowed_source", "varchar", max_length=64),
    _nullable("worker_install_keys", "registration_id", "varchar", max_length=32),
    _nullable("worker_install_keys", "recovery_secret_hash", "varchar", max_length=64),
    _nullable("worker_install_keys", "registration_request_hash", "varchar", max_length=64),
    _nullable("worker_install_keys", "credential_derivation_version", "int2"),
    _nullable("worker_install_keys", "recovery_expires_at", "timestamptz"),
    _nullable("worker_install_keys", "registration_acknowledged_at", "timestamptz"),
    _nullable("audit_logs", "user_id", "int8"),
    _required("scheduled_tasks", "project_id", "int8", check_default=False),
    _nullable("project_rules", "region", "varchar", max_length=50),
    _required("project_rules", "require_render", "bool", default="false"),
    _required("task_run_lease_generations", "id", "int8", check_default=False),
    _required("task_run_lease_generations", "run_id", "varchar", max_length=64),
    _required("task_run_lease_generations", "worker_id", "int8"),
    _required("task_run_lease_generations", "lease_id", "varchar", max_length=64),
    _nullable("task_run_lease_generations", "lease_gen", "int8"),
    _nullable("task_run_lease_generations", "log_valid_through_id", "varchar", max_length=41),
    _required("task_run_lease_generations", "created_at", "timestamptz", default="current_timestamp"),
    _nullable("task_run_lease_generations", "closed_at", "timestamptz"),
)


INDEX_CONTRACTS = (
    IndexContract(name="idx_workers_api_key_hash", table="workers", keys=("api_key_hash",)),
    IndexContract(
        name="idx_workers_api_key_previous_hash",
        table="workers",
        keys=("api_key_previous_hash",),
    ),
    IndexContract(
        name="idx_task_executions_crawl_batch_id",
        table="task_executions",
        keys=("result_data ->> 'crawl_batch_id'",),
        predicate="result_data ->> 'crawl_batch_id' IS NOT NULL",
    ),
    IndexContract(
        name="idx_task_executions_crawl_batch_status",
        table="task_executions",
        keys=("result_data ->> 'crawl_batch_id'", "status"),
        predicate="result_data ->> 'crawl_batch_id' IS NOT NULL",
    ),
    IndexContract(
        name="idx_crawl_batches_status_created",
        table="crawl_batches",
        keys=("status", "created_at DESC"),
    ),
    IndexContract(
        name="idx_task_logs_event_id_unique",
        table="task_logs",
        keys=("event_id",),
        unique=True,
        predicate="event_id IS NOT NULL",
    ),
    IndexContract(name="idx_task_logs_run_id_id", table="task_logs", keys=("run_id", "id")),
    IndexContract(
        name="idx_worker_install_keys_unacknowledged_recovery",
        table="worker_install_keys",
        keys=("recovery_expires_at",),
        predicate="status = 'used' AND registration_acknowledged_at IS NULL",
    ),
    IndexContract(name="idx_task_executions_lease_gen", table="task_executions", keys=("lease_gen",)),
    IndexContract(
        name="idx_worker_install_keys_registration_id_unique",
        table="worker_install_keys",
        keys=("registration_id",),
        unique=True,
        predicate="registration_id IS NOT NULL",
    ),
    IndexContract(
        name="idx_task_executions_cancel_requested_at",
        table="task_executions",
        keys=("cancel_requested_at",),
    ),
    IndexContract(
        name="idx_task_executions_scheduler_fencing_token",
        table="task_executions",
        keys=("scheduler_fencing_token",),
    ),
    IndexContract(name="idx_project_rules_region", table="project_rules", keys=("region",)),
    IndexContract(
        name="idx_project_sources_repository_subdir",
        table="project_sources",
        keys=("repository_id", "subdir"),
    ),
    IndexContract(
        name="idx_scheduled_tasks_project_id",
        table="scheduled_tasks",
        keys=("project_id",),
    ),
)

GENERATED_PRIMARY_KEYS = (
    GeneratedPrimaryKeyContract(
        table="task_run_lease_generations",
        columns=("id",),
        generated_column="id",
    ),
)

UNIQUE_COLUMNS = (UniqueColumnsContract(table="task_run_lease_generations", columns=("run_id", "lease_id")),)

FOREIGN_KEYS = (
    ForeignKeyContract(
        name="fk_scheduled_tasks_project_id",
        table="scheduled_tasks",
        columns=("project_id",),
        referenced_table="projects",
        referenced_columns=("id",),
        delete_action="r",
    ),
)

__all__ = [
    "COLUMN_CONTRACTS",
    "FOREIGN_KEYS",
    "GENERATED_PRIMARY_KEYS",
    "INDEX_CONTRACTS",
    "UNIQUE_COLUMNS",
    "ColumnContract",
    "ForeignKeyContract",
    "GeneratedPrimaryKeyContract",
    "IndexContract",
    "UniqueColumnsContract",
]
