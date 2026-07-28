-- 加列可以放在事务里；建索引必须走 CONCURRENTLY 且不能在事务块内。
--
-- P1-DB-07: 第二阶段(CONCURRENTLY 索引)失败时第一阶段已提交不可回滚。
-- 把可预判失败(既有列类型与索引不兼容)前置到第一阶段事务内校验:
-- 预检不过 → RAISE → 整体未应用;预检通过 → 第二阶段仅剩运维级中断风险。
BEGIN;

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_name = 'task_executions'
           AND (
                (column_name = 'lease_id' AND data_type <> 'character varying')
             OR (column_name = 'worker_id' AND data_type NOT IN ('bigint', 'integer'))
             OR (column_name = 'status' AND data_type NOT IN ('character varying', 'text'))
           )
    LOOP
        RAISE EXCEPTION 'task_executions.% 类型不兼容(%)，索引无法创建，拒绝迁移（此时尚未提交任何变更）',
            bad.column_name, bad.data_type;
    END LOOP;
END $$;

ALTER TABLE "task_executions"
    ADD COLUMN IF NOT EXISTS "lease_id" VARCHAR(64) NULL;

COMMIT;

-- task_executions 是热写表：非并发建索引会拿 SHARE 锁，升级期间阻塞所有写入。
-- CREATE INDEX CONCURRENTLY 不能包在事务块里，以下两条必须作为独立语句执行
-- （与 20260717_add_task_logs_run_id_id_index.sql 同一套路）。
-- 恢复：CONCURRENTLY 中途被打断可能残留永久 INVALID 索引，重跑前先查出并删除：
--   SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
--     WHERE i.indisvalid = false
--       AND c.relname IN ('idx_task_executions_lease_id',
--                         'idx_task_executions_worker_lease_status');
--   DROP INDEX CONCURRENTLY IF EXISTS "idx_task_executions_lease_id";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_lease_id"
    ON "task_executions" ("lease_id");

CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_worker_lease_status"
    ON "task_executions" ("worker_id", "lease_id", "status");
