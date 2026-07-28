-- 加列可以放在事务里；建索引必须走 CONCURRENTLY 且不能在事务块内。
--
-- P1-DB-07: 两阶段迁移的全回滚语义只在"第一阶段失败"时成立——第二阶段
-- (CONCURRENTLY 索引) 失败时第一阶段已提交、不可回滚。因此把所有可预判的
-- 失败(目标列已存在但类型不兼容,索引必然建不出来)前置到第一阶段事务内
-- 显式校验:预检不过 → RAISE → 整体未应用;预检通过 → 第二阶段仅剩
-- 运维级中断风险(恢复步骤见下方注释)。
BEGIN;

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_name = 'scheduler_outbox'
           AND (
                (column_name = 'consumed_at' AND data_type <> 'timestamp with time zone')
             OR (column_name = 'consume_owner' AND data_type <> 'character varying')
             OR (column_name = 'consume_started_at' AND data_type <> 'timestamp with time zone')
           )
    LOOP
        RAISE EXCEPTION 'scheduler_outbox.% 已存在且类型不兼容(%)，拒绝迁移（此时尚未提交任何变更）',
            bad.column_name, bad.data_type;
    END LOOP;
END $$;

ALTER TABLE "scheduler_outbox"
    ADD COLUMN IF NOT EXISTS "consumed_at" TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS "consume_owner" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "consume_started_at" TIMESTAMPTZ NULL;

COMMIT;

-- scheduler_outbox 写入频繁：非并发建索引会阻塞投递写入。
-- CREATE INDEX CONCURRENTLY 不能包在事务块里，必须作为独立语句执行。
-- 恢复：CONCURRENTLY 中途被打断可能残留永久 INVALID 索引，重跑前先查出并删除：
--   SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
--     WHERE i.indisvalid = false AND c.relname = 'idx_scheduler_outbox_consumption';
--   DROP INDEX CONCURRENTLY IF EXISTS "idx_scheduler_outbox_consumption";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_scheduler_outbox_consumption"
    ON "scheduler_outbox" ("consumed_at", "consume_started_at");
