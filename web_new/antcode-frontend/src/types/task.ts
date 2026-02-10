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

// 分发状态 - 匹配后端
export type DispatchStatus =
  | 'pending'
  | 'dispatching'
  | 'dispatched'
  | 'acked'
  | 'rejected'
  | 'timeout'
  | 'failed'

// 运行状态 - 匹配后端
export type RuntimeStatus =
  | 'queued'
  | 'running'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'timeout'
  | 'skipped'

// 任务类型 - 匹配后端
export type TaskType = 'file' | 'code' | 'rule'

// 调度类型 - 匹配后端
export type ScheduleType = 'once' | 'interval' | 'cron'

// 任务执行策略选项（用于UI展示）
export const TASK_EXECUTION_STRATEGY_OPTIONS = [
  { value: '', label: '继承项目配置', description: '使用项目的执行策略配置' },
  { value: 'specified', label: '指定 Worker', description: '在指定的 Worker 上执行' },
  { value: 'auto', label: '自动选择', description: '根据负载自动选择 Worker' },
] as const

// 基础任务接口 - 匹配后端API
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

  // 执行配置（后端返回支持时可用）
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

  // 执行策略配置
  execution_strategy?: ExecutionStrategy
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

  // 统计信息（如已加载）
  success_count?: number
  failure_count?: number

  // 关联数据
  project?: Project
  runs?: TaskRun[]
}

// 任务运行记录 - 匹配后端API
export interface TaskRun {
  id: string
  task_id: string
  execution_id: string
  start_time?: string
  end_time?: string
  duration_seconds?: number
  status: TaskStatus
  dispatch_status: DispatchStatus
  runtime_status?: RuntimeStatus
  dispatch_updated_at?: string
  runtime_updated_at?: string
  exit_code?: number
  error_message?: string
  result_data?: Record<string, unknown>
  stdout?: string
  stderr?: string
  worker_id?: string
  retry_count?: number
  created_at?: string
  updated_at?: string
}

// 创建任务请求 - 匹配后端API
export interface TaskCreateRequest {
  name: string
  description?: string
  project_id: string
  schedule_type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string
  max_instances?: number
  timeout_seconds?: number
  retry_count?: number
  retry_delay?: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>
  is_active?: boolean

  // 执行策略配置
  execution_strategy?: ExecutionStrategy
  specified_worker_id?: string
}

// 更新任务请求 - 匹配后端API
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

  // 执行策略配置
  execution_strategy?: ExecutionStrategy
  specified_worker_id?: string
}

// 任务列表响应 - 匹配后端API
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
  priority?: TaskPriority
  trigger_type?: TaskTriggerType
  search?: string
  date_range?: {
    start: string
    end: string
  }
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  specified_worker_id?: string
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

// 任务统计信息（单任务）
export interface TaskStats {
  total_executions: number
  success_count: number
  failed_count: number
  success_rate: number
  average_duration: number
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

// 任务优先级
export type TaskPriority = 'low' | 'normal' | 'high' | 'urgent'

// 任务触发类型
export type TaskTriggerType = 'manual' | 'scheduled' | 'webhook' | 'api'

// 调度配置
export interface ScheduleConfig {
  type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string
  timezone?: string
}

// 批量操作请求
export interface TaskBatchRequest {
  task_ids: string[]
  action: 'start' | 'stop' | 'cancel' | 'delete' | 'enable' | 'disable'
  execution_config?: Partial<ExecutionConfig>
}
