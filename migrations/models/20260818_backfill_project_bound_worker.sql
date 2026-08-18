-- 回填 projects.bound_worker_id：把「运行时在哪个 Worker」补成「调度绑定到哪个 Worker」。
--
-- 成因：创建项目走的 `_bind_worker_runtime`（project_runtime_binding.py）只写了
-- `projects.worker_id`（Worker.public_id 字符串）与 `worker_env_name`，从不写
-- `projects.bound_worker_id`（Worker.id 内部整型）。而项目默认执行策略是 `prefer`，
-- `execution_resolver._resolve_prefer_bound` 唯一判据就是 `bound_worker_id`：为 NULL
-- 时直接落回按负载自动选节点。集群只有 1 个 Worker 时看不出来（挑来挑去都是它），
-- 一旦有第 2 个节点，任务就会被派到**没有该运行时环境**的 Worker 上，必现
-- `运行时环境不存在: <env>`，重试全部同样失败。
--
-- 代码侧已在 `_bind_worker_runtime` 补写 `bound_worker_id`；本脚本只负责把存量项目
-- 补齐，使升级后的老项目与新建项目行为一致。可重复执行。
--
-- 刻意不回填的三类，全部是「显式意图」而非缺失，静默改写它们才是错的：
--   1. `bound_worker_id` 已有值      —— 用户/导入路径已经绑过，不覆盖；
--   2. `execution_strategy = 'auto'` —— unified_project_service._resolve_execution_binding
--      对 auto 策略显式写 NULL 表示「不要绑定」，回填会推翻这个决定；
--   3. `worker_id` 找不到对应 Worker  —— 节点已被删除（worker_service 级联会把
--      `worker_id` 一并置 NULL），没有可绑定的目标，留给下面的守卫报出来。

DO $$
DECLARE
    backfilled_count INTEGER;
    dangling_count INTEGER;
BEGIN
    UPDATE public.projects AS project
       SET bound_worker_id = worker.id,
           updated_at = NOW()
      FROM public.workers AS worker
     WHERE project.worker_id = worker.public_id
       AND project.bound_worker_id IS NULL
       AND project.execution_strategy <> 'auto';

    GET DIAGNOSTICS backfilled_count = ROW_COUNT;
    RAISE NOTICE 'projects.bound_worker_id 已回填 % 行', backfilled_count;

    -- 守卫：`worker_id` 指向一个 workers 表里不存在的 public_id，说明级联解绑漏了。
    -- 这类项目的运行时环境已无处可寻，派发必然失败，必须让人看见而不是留在库里。
    SELECT count(*)
      INTO dangling_count
      FROM public.projects AS project
     WHERE project.worker_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM public.workers AS worker WHERE worker.public_id = project.worker_id
       );

    IF dangling_count > 0 THEN
        RAISE EXCEPTION
            'projects.worker_id 有 % 行指向不存在的 Worker，运行时环境已不可达，请人工核对后再上线',
            dangling_count;
    END IF;
END $$;
