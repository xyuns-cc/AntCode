-- 摘掉死列 projects.runtime_worker_id。
--
-- 成因：这列自建表起就没有任何写入方。全仓唯一的写是 worker_service
-- `_cascade_delete_worker_data` 里的置 NULL（只能 非NULL→NULL，永远造不出值），
-- 唯一的传播是 project_duplicate 复制项目时把 NULL 抄成 NULL。三条建项目路径
-- （project_service.create_project → _bind_worker_runtime、repository_import_service、
-- project_duplicate）都不写它，因此每一行恒为 NULL。
--
-- 它想表达的语义（"环境所在 Worker 节点内部 ID"）已经由 `bound_worker_id` 承担：
-- 同为 BigInt、同指 workers.id，且被 _bind_worker_runtime / repository_import_service /
-- unified_project_service 三处真实写入。20260818 那次多 Worker 派发故障的修法也是
-- 补写 `bound_worker_id`——没有人把 `runtime_worker_id` 当成数据来源。保留它等于给
-- "运行时在哪个 Worker" 留两个可以分叉的真源，所以这里选择删除而不是补写。
--
-- 代码侧连带清掉 scheduler_service 里 `Q(runtime_worker_id=worker.id)` 那条恒不
-- 命中的 OR 分支。可重复执行。

DO $$
DECLARE
    non_null_count INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'projects' AND column_name = 'runtime_worker_id'
    ) THEN
        RAISE NOTICE 'projects.runtime_worker_id 不存在，跳过';
        RETURN;
    END IF;

    -- 守卫：理论上恒为 NULL。真有值说明存在本次排查没找到的写入方，
    -- 此时删列会丢数据，必须让人看见而不是静默删掉。
    EXECUTE 'SELECT count(*) FROM public.projects WHERE runtime_worker_id IS NOT NULL'
       INTO non_null_count;

    IF non_null_count > 0 THEN
        RAISE EXCEPTION
            'projects.runtime_worker_id 有 % 行非 NULL，与"无写入方"的判定矛盾，请先核对写入来源再删列',
            non_null_count;
    END IF;

    -- 索引名由 Tortoise 生成（含哈希后缀），不写死：DROP COLUMN 会连带删掉
    -- 所有只涉及该列的索引。
    ALTER TABLE public.projects DROP COLUMN runtime_worker_id;
    RAISE NOTICE 'projects.runtime_worker_id 已删除';
END $$;
