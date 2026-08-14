-- 持久化已分配 TaskRun 的取消请求，不提前伪造 Worker 执行终态。
-- 索引必须在事务外并发创建，避免升级时阻塞 task_executions 写入。

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type, is_nullable
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'task_executions'
           AND column_name IN ('cancel_requested_at', 'cancel_requested_by')
           AND (
               (column_name = 'cancel_requested_at' AND data_type <> 'timestamp with time zone')
               OR (column_name = 'cancel_requested_by' AND data_type <> 'bigint')
               OR is_nullable <> 'YES'
           )
    LOOP
        RAISE EXCEPTION 'task_executions.% 已存在且类型或可空性不兼容(%, %)，拒绝迁移',
            bad.column_name, bad.data_type, bad.is_nullable;
    END LOOP;
END $$;

ALTER TABLE task_executions
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cancel_requested_by BIGINT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_executions_cancel_requested_at
    ON task_executions (cancel_requested_at);
