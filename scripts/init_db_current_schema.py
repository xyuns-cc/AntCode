"""Current schema additions required by both fresh and legacy databases."""

CURRENT_SCHEMA_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_workers_api_key_hash",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_workers_api_key_hash"
            ON public."workers" ("api_key_hash")
        """,
    ),
    (
        "idx_workers_api_key_previous_hash",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_workers_api_key_previous_hash"
            ON public."workers" ("api_key_previous_hash")
        """,
    ),
    (
        "idx_task_executions_lease_gen",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_lease_gen"
            ON public."task_executions" ("lease_gen")
        """,
    ),
    (
        "idx_worker_install_keys_registration_id_unique",
        """
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "idx_worker_install_keys_registration_id_unique"
            ON public."worker_install_keys" ("registration_id")
            WHERE "registration_id" IS NOT NULL
        """,
    ),
    (
        "idx_task_executions_cancel_requested_at",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_cancel_requested_at"
            ON public."task_executions" ("cancel_requested_at")
        """,
    ),
    (
        "idx_task_executions_scheduler_fencing_token",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_scheduler_fencing_token"
            ON public."task_executions" ("scheduler_fencing_token")
        """,
    ),
    (
        "idx_project_rules_region",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_project_rules_region"
            ON public."project_rules" ("region")
        """,
    ),
    (
        "idx_project_sources_repository_subdir",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_project_sources_repository_subdir"
            ON public."project_sources" ("repository_id", "subdir")
        """,
    ),
    (
        "idx_scheduled_tasks_project_id",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_scheduled_tasks_project_id"
            ON public."scheduled_tasks" ("project_id")
        """,
    ),
)

CURRENT_SCHEMA_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (
        "task_executions",
        "cancel_requested_at",
        'ALTER TABLE public."task_executions" ADD COLUMN IF NOT EXISTS "cancel_requested_at" TIMESTAMPTZ NULL',
    ),
    (
        "task_executions",
        "cancel_requested_by",
        'ALTER TABLE public."task_executions" ADD COLUMN IF NOT EXISTS "cancel_requested_by" BIGINT NULL',
    ),
    (
        "task_executions",
        "scheduler_fencing_token",
        'ALTER TABLE public."task_executions" ADD COLUMN IF NOT EXISTS "scheduler_fencing_token" BIGINT NULL',
    ),
    (
        "project_rules",
        "region",
        'ALTER TABLE public."project_rules" ADD COLUMN IF NOT EXISTS "region" VARCHAR(50) NULL',
    ),
    (
        "project_rules",
        "require_render",
        'ALTER TABLE public."project_rules" ADD COLUMN IF NOT EXISTS "require_render" BOOLEAN NOT NULL DEFAULT FALSE',
    ),
)

__all__ = ["CURRENT_SCHEMA_COLUMNS", "CURRENT_SCHEMA_INDEXES"]
