-- Fence Master scheduler writes with a PostgreSQL epoch and persist each run's
-- originating/takeover epoch. Existing runs remain NULL and are not treated as
-- recoverable scheduler-owned runs until a current Leader explicitly claims them.

BEGIN;

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type, is_nullable
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'task_executions'
           AND column_name = 'scheduler_fencing_token'
           AND (data_type <> 'bigint' OR is_nullable <> 'YES')
    LOOP
        RAISE EXCEPTION 'task_executions.% has incompatible type/nullability (%, %)',
            bad.column_name, bad.data_type, bad.is_nullable;
    END LOOP;
END $$;

ALTER TABLE task_executions
    ADD COLUMN IF NOT EXISTS scheduler_fencing_token BIGINT NULL;

CREATE TABLE IF NOT EXISTS scheduler_authority (
    name VARCHAR(32) PRIMARY KEY,
    fencing_token BIGINT NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL
);

COMMIT;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_executions_scheduler_fencing_token
    ON task_executions (scheduler_fencing_token);
