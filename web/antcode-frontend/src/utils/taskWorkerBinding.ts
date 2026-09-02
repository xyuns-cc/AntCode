import type { ExecutionStrategy } from '@/types'

/**
 * 任务的 **Worker 绑定** 文案 —— 配置值，不是生效值。
 *
 * 后端 `TaskResponse` 只有两组绑定字段（`specified_worker_*` = 任务级指定、
 * `project_bound_worker_*` = 项目级绑定），没有任何「这一次实际跑在哪台」。实际执行的
 * Worker 记在 run 上（`TaskRunResponse.worker_id`，见 schemas/task.py），只能按 run 逐条
 * 取；任务列表接口拿不到，`GET /tasks/running` 虽然带 `worker_id` 却给的是内部自增 ID，
 * 和 Worker 列表用的 public_id 对不上。所以调用点的列名只能说「绑定」。
 *
 * `auto` 策略下没有绑定 **不等于「未分配」**：调度器每次按负载现挑一台，任务照常在跑。
 * 把它显示成「未分配」会让人以为任务卡住了，而真相是这台任务根本不需要绑定。
 */
export interface TaskWorkerBindingFields {
  execution_strategy?: ExecutionStrategy | null
  specified_worker_id?: string | null
  specified_worker_name?: string | null
  project_execution_strategy?: ExecutionStrategy | null
  project_bound_worker_id?: string | null
  project_bound_worker_name?: string | null
}

export const describeTaskWorkerBinding = (task: TaskWorkerBindingFields): string => {
  const strategy = task.execution_strategy || task.project_execution_strategy
  if (strategy === 'auto') return '自动选择'
  if (strategy === 'specified') {
    return task.specified_worker_name || task.specified_worker_id || '指定 Worker'
  }
  if (strategy === 'fixed' || strategy === 'prefer') {
    return task.project_bound_worker_name || task.project_bound_worker_id || '绑定 Worker'
  }
  return '本地'
}
