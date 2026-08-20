-- 摘掉死列 task_executions.cpu_usage / task_executions.memory_usage。
--
-- 成因：两列自建表起就没有写入方，`git log -S cpu_usage` 只在项目初始化那几次提交
-- 里见过它们，之后再没被碰过。全仓 grep（packages/ services/ scripts/ migrations/
-- contracts/ tests/ web/）里同名命中全在别处：前端 dashboard 的
-- `systemMetrics.cpu_usage.percent` 由 `/system/metrics` 的 cpu_percent 换算而来，
-- log_performance_service 的同名字段是进程内性能采样 dataclass，两者都与
-- task_executions 无关；TaskRunResponse.from_orm 是逐字段枚举，也没有它们。
-- 真机 antcode 库 155 行 task_executions，两列非空计数都是 0。
--
-- "任务级资源用量"断在哪：Worker 侧确实采到了（executor/resource_sampler.py →
-- ProcessInfo.cpu_time_seconds / memory_peak_mb），但那份数字只在 Worker 内部当
-- CPU/内存超限判据用（process.py 的 _describe_limit_breach → SIGKILL、
-- _limit_breach_result → ExitReason.CPU_LIMIT/OOM）。engine.py::_task_result 组装
-- 上报用的 TaskResult 时没有把它们放进 data，数字压根没离开 Worker。把这两列补活
-- 等于新造一整条 Worker→Master→API 上报链路，那是做功能不是修列；在那之前留着恒
-- NULL 的列只是埋一个"看着有其实没有"的假信号。与 20260819 删
-- projects.runtime_worker_id 同型，这里同样选择删除。
--
-- 可重复执行。

DO $$
DECLARE
    dead_columns TEXT[] := ARRAY['cpu_usage', 'memory_usage'];
    dead_column TEXT;
    non_null_count INTEGER;
BEGIN
    FOREACH dead_column IN ARRAY dead_columns LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'task_executions'
               AND column_name = dead_column
        ) THEN
            RAISE NOTICE 'task_executions.% 不存在，跳过', dead_column;
            CONTINUE;
        END IF;

        -- 守卫：理论上恒为 NULL。真有值说明存在本次排查没找到的写入方，
        -- 此时删列会丢数据，必须让人看见而不是静默删掉。整块是单条 DO 语句，
        -- 异常会把本轮之前已经删掉的列一并回滚，不留"删了一半"的中间态。
        EXECUTE format('SELECT count(*) FROM public.task_executions WHERE %I IS NOT NULL', dead_column)
           INTO non_null_count;

        IF non_null_count > 0 THEN
            RAISE EXCEPTION
                'task_executions.% 有 % 行非 NULL，与"无写入方"的判定矛盾，请先核对写入来源再删列',
                dead_column, non_null_count;
        END IF;

        -- 索引名由 Tortoise 生成（含哈希后缀），不写死：DROP COLUMN 会连带删掉
        -- 所有只涉及该列的索引。
        EXECUTE format('ALTER TABLE public.task_executions DROP COLUMN %I', dead_column);
        RAISE NOTICE 'task_executions.% 已删除', dead_column;
    END LOOP;
END $$;
