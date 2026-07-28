-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
-- Execute this migration as a standalone statement (psql -f runs it in
-- autocommit; init_db.py runs it on a non-transactional Tortoise connection).
--
-- Recovery: CONCURRENTLY + IF NOT EXISTS can leave a permanently INVALID index
-- if the build is interrupted. Detect it via pg_index.indisvalid and drop
-- before rerunning:
--   SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
--     WHERE i.indisvalid = false AND c.relname = 'idx_task_logs_run_id_id';
--   DROP INDEX CONCURRENTLY IF EXISTS "idx_task_logs_run_id_id";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_logs_run_id_id"
    ON "task_logs" ("run_id", "id");
