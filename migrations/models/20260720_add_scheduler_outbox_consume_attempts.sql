-- P2 §4.5: 消费侧重投计数与发布侧 attempts 分离。
-- 单阶段纯加列迁移，可整体回滚。与 20260713 同一防护模式：既有列类型
-- 不兼容时在事务内前置 RAISE，避免 IF NOT EXISTS 静默错型。
BEGIN;

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_name = 'scheduler_outbox'
           AND column_name = 'consume_attempts'
           AND data_type NOT IN ('integer', 'bigint')
    LOOP
        RAISE EXCEPTION 'scheduler_outbox.% 已存在且类型不兼容(%)，拒绝迁移（此时尚未提交任何变更）',
            bad.column_name, bad.data_type;
    END LOOP;
END $$;

ALTER TABLE "scheduler_outbox"
    ADD COLUMN IF NOT EXISTS "consume_attempts" INTEGER NOT NULL DEFAULT 0;

COMMIT;
