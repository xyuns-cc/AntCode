CREATE TABLE IF NOT EXISTS "task_logs" (
    "id" BIGSERIAL PRIMARY KEY,
    "event_id" VARCHAR(128) NULL,
    "run_id" VARCHAR(64) NOT NULL,
    "log_type" VARCHAR(16) NOT NULL DEFAULT 'stdout',
    "content" TEXT NOT NULL,
    "sequence" BIGINT NOT NULL DEFAULT 0,
    "timestamp" TIMESTAMPTZ NOT NULL,
    "level" VARCHAR(16) NOT NULL DEFAULT 'INFO',
    "source" VARCHAR(128) NOT NULL DEFAULT 'task_execution',
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "idx_task_logs_run_sequence"
    ON "task_logs" ("run_id", "sequence");

CREATE INDEX IF NOT EXISTS "idx_task_logs_run_type"
    ON "task_logs" ("run_id", "log_type");

CREATE INDEX IF NOT EXISTS "idx_task_logs_timestamp"
    ON "task_logs" ("timestamp");

CREATE UNIQUE INDEX IF NOT EXISTS "idx_task_logs_event_id_unique"
    ON "task_logs" ("event_id")
    WHERE "event_id" IS NOT NULL;
