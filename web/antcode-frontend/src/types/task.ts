import type { Project, ExecutionStrategy } from './project'

// 任务状态枚举 - 匹配后端
export type TaskStatus =
  | 'pending'
  | 'dispatching'
  | 'queued'
  | 'running'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'timeout'
  | 'paused'
  | 'rejected'
  | 'skipped'

// 任务类型 - 匹配后端
export type TaskType = 'file' | 'code' | 'rule' | 'spider'

// 调度类型 - 匹配后端
export type ScheduleType = 'once' | 'interval' | 'cron' | 'date'

// 任务执行策略选项（用于UI展示）
export const TASK_EXECUTION_STRATEGY_OPTIONS = [
  { value: '', label: '继承项目配置', description: '使用项目的执行策略配置' },
  { value: 'fixed', label: '固定 Worker', description: '仅在绑定 Worker 执行，不可用时失败' },
  { value: 'specified', label: '指定 Worker', description: '在指定的 Worker 上执行' },
  { value: 'auto', label: '自动选择', description: '根据负载自动选择 Worker' },
  { value: 'prefer', label: '优先绑定 Worker', description: '优先绑定 Worker，不可用时自动选择其他 Worker' },
] as const

// 基础任务接口 - 对齐后端 TaskResponse
export interface Task {
  id: string
  name: string
  description?: string
  project_id: string
  task_type: TaskType

  // 调度配置
  schedule_type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string

  // 执行配置（部分接口可能不返回，保持可选）
  max_instances?: number
  timeout_seconds?: number
  retry_count?: number
  retry_delay?: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>

  // 状态信息
  status: TaskStatus
  is_active: boolean
  last_run_time?: string
  next_run_time?: string
  failure_count?: number
  success_count?: number

  // 执行策略配置
  execution_strategy?: ExecutionStrategy | null
  specified_worker_id?: string
  specified_worker_name?: string

  // 项目执行配置（继承自项目）
  project_execution_strategy?: ExecutionStrategy
  project_bound_worker_id?: string
  project_bound_worker_name?: string

  // 时间戳
  created_at: string
  updated_at: string
  user_id?: string
  created_by: string
  created_by_username?: string

  // 关联数据
  project?: Project
  executions?: TaskExecution[]
}

// 任务执行记录（对齐后端 TaskRunResponse，保留向前兼容字段为可选）
export interface TaskExecution {
  id: string
  task_id: string
  execution_id: string

  start_time: string
  end_time?: string
  duration_seconds?: number

  status: TaskStatus
  dispatch_status?: string
  runtime_status?: string
  dispatch_updated_at?: string
  runtime_updated_at?: string

  exit_code?: number
  error_message?: string
  result_data?: Record<string, unknown>
  stdout?: string
  stderr?: string

  retry_count?: number
  worker_id?: string

  created_at?: string
  updated_at?: string
}

// 创建任务请求 - 匹配后端
export interface TaskCreateRequest {
  name: string
  description?: string
  project_id: string
  schedule_type: ScheduleType
  is_active?: boolean

  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string

  max_instances?: number
  timeout_seconds?: number
  retry_count?: number
  retry_delay?: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>

  // 执行策略配置
  execution_strategy?: ExecutionStrategy | null
  specified_worker_id?: string
}

// 更新任务请求 - 匹配后端
export interface TaskUpdateRequest {
  name?: string
  description?: string
  schedule_type?: ScheduleType
  is_active?: boolean

  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string

  max_instances?: number
  timeout_seconds?: number
  retry_count?: number
  retry_delay?: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>

  execution_strategy?: ExecutionStrategy | null
  specified_worker_id?: string
}

// 任务列表响应 - 匹配后端
export interface TaskListResponse {
  total: number
  page: number
  size: number
  items: Task[]
}

// 任务列表查询参数
export interface TaskListParams {
  page?: number
  size?: number
  project_id?: string
  status?: TaskStatus
  schedule_type?: ScheduleType
  search?: string
  specified_worker_id?: string

  sort_by?: string
  sort_order?: 'asc' | 'desc'
}

// 任务结果
export interface TaskResult {
  success: boolean
  message?: string
  data?: unknown
  output_files?: string[]
  metrics?: {
    items_processed?: number
    items_success?: number
    items_failed?: number
    processing_rate?: number
  }
}

// 任务日志
export interface TaskLog {
  id: string
  execution_id: string
  level: 'debug' | 'info' | 'warning' | 'error'
  message: string
  timestamp: string
  source?: string
  context?: Record<string, unknown>
}

// 任务优先级（前端筛选保留）
export type TaskPriority = 'low' | 'normal' | 'high' | 'urgent'

// 任务触发类型（前端筛选保留）
export type TaskTriggerType = 'manual' | 'scheduled' | 'webhook' | 'api'

// 调度配置
export interface ScheduleConfig {
  type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string
  timezone?: string
}

// 执行配置
export interface ExecutionConfig {
  max_instances: number
  timeout_seconds: number
  retry_count: number
  retry_delay: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>
}

// 任务执行请求
export interface TaskExecuteRequest {
  task_id: string
  execution_config?: Partial<ExecutionConfig>
  environment_variables?: Record<string, string>
}

// 分页数据（不包含 BaseResponse 外层包装）
export interface PageData<T> {
  items: T[]
  total: number
  page: number
  size: number
  pages: number
}

// 任务统计信息
export interface TaskStats {
  total_tasks: number
  pending_tasks: number
  running_tasks: number
  completed_tasks: number
  failed_tasks: number
  cancelled_tasks: number

  tasks_by_priority: {
    low: number
    normal: number
    high: number
    urgent: number
  }

  tasks_by_type: {
    manual: number
    scheduled: number
    webhook: number
    api: number
  }

  recent_executions: TaskExecution[]
}
