-- P1-GW-04: TaskRun 增加 lease_gen(BIGINT NULL) 用于 lease 代际单调 CAS。
--
-- 背景：run_ownership fence(Lua)返回 ACQUIRED 后 Gateway 调
-- bind_worker_run_lease_generation 把 PG 绑定到当前代际。原实现只按
-- (run_id, worker_id) 更新 lease_id，若 L1 fence 返回 ACQUIRED 后异常暂停，
-- L2 完成 fence + bind 后 L1 迟到 bind 会把 PG 从 L2 覆盖回 L1。
--
-- 修复：新增 lease_gen（fence 时点的 Unix ms），CAS 谓词
-- `lease_gen IS NULL OR lease_gen <= NEW.lease_gen` 确保只允许更晚 gen
-- 覆盖更早 gen。旧代际 L1 用较小 gen 迟到时 CAS 失败，PG 保留 L2 状态。
--
-- 列变更在短事务中完成；热表索引必须在事务外并发创建。
BEGIN;

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_name = 'task_executions'
           AND column_name = 'lease_gen'
           AND data_type NOT IN ('bigint')
    LOOP
        RAISE EXCEPTION 'task_executions.% 已存在且类型不兼容(%)，拒绝迁移（此时尚未提交任何变更）',
            bad.column_name, bad.data_type;
    END LOOP;
END $$;

ALTER TABLE "task_executions"
    ADD COLUMN IF NOT EXISTS "lease_gen" BIGINT NULL;

COMMIT;

-- CREATE INDEX CONCURRENTLY 不能位于 BEGIN/COMMIT。若构建中断留下
-- INVALID 同名索引，先 DROP INDEX CONCURRENTLY 后重跑本文件。
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_executions_lease_gen"
    ON "task_executions" ("lease_gen");
