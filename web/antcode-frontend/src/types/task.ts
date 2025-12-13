import type { Project, ExecutionStrategy } from './project'

// 任务状态枚举 - 匹配后端
export type TaskStatus = 
  | 'pending'       // 等待调度
  | 'dispatching'   // 正在分配节点
  | 'queued'        // 已分发到节点队列，等待执行
  | 'running'       // 正在执行
  | 'success'       // 执行成功
  | 'completed'     // 执行完成（兼容旧状态）
  | 'failed'        // 执行失败
  | 'cancelled'     // 已取消
  | 'timeout'       // 执行超时
  | 'paused'        // 已暂停

// 任务类型 - 匹配后端
export type TaskType = 'code' | 'rule'

// 调度类型 - 匹配后端
export type ScheduleType = 'once' | 'interval' | 'cron'

// 任务执行策略选项（用于UI展示）
export const TASK_EXECUTION_STRATEGY_OPTIONS = [
  { value: '', label: '继承项目配置', description: '使用项目的执行策略配置' },
  { value: 'local', label: '本地执行', description: '在主节点本地执行' },
  { value: 'specified', label: '指定节点', description: '在指定的节点上执行' },
  { value: 'auto', label: '自动选择', description: '根据负载自动选择节点' },
] as const

// 基础任务接口 - 匹配后端API
export interface Task {
  id: string  // public_id
  name: string
  description?: string
  project_id: string  // public_id
  task_type: TaskType

  // 调度配置
  schedule_type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  scheduled_time?: string

  // 执行配置
  max_instances: number
  timeout_seconds: number
  retry_count: number
  retry_delay: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>

  // 状态信息
  status: TaskStatus
  is_active: boolean
  last_run_time?: string
  next_run_time?: string
  failure_count: number
  success_count: number

  // 执行策略配置
  execution_strategy?: ExecutionStrategy | null  // 为空则继承项目配置
  specified_node_id?: string      // 指定执行节点ID（仅 specified 策略）
  specified_node_name?: string    // 指定执行节点名称
  
  // 项目执行配置（继承自项目）
  project_execution_strategy?: ExecutionStrategy
  project_bound_node_id?: string
  project_bound_node_name?: string

  // 兼容旧字段
  node_id?: string      // [已废弃] 指定执行节点ID
  node_name?: string    // [已废弃] 执行节点名称

  // 时间戳
  created_at: string
  updated_at: string
  user_id: string  // public_id
  created_by: string  // public_id
  created_by_username?: string

  // 关联数据
  project?: Project
  executions?: TaskExecution[]
}

// 任务执行记录 - 匹配后端API
export interface TaskExecution {
  id: string  // public_id
  task_id: string  // public_id
  execution_id: string
  start_time: string
  end_time?: string
  duration_seconds?: number
  status: TaskStatus
  exit_code?: number
  error_message?: string
  result_data?: Record<string, unknown>
  stdout?: string
  stderr?: string
  retry_count: number
  created_at: string
  updated_at: string
}

// 创建任务请求 - 匹配后端API
export interface TaskCreateRequest {
  name: string
  description?: string
  project_id: string  // public_id
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
  execution_strategy?: ExecutionStrategy | null  // 为空则继承项目配置
  specified_node_id?: string    // 指定执行节点ID（仅 specified 策略）
  
  // 兼容旧字段
  node_id?: string    // [已废弃] 指定执行节点ID
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
  execution_strategy?: ExecutionStrategy | null
  specified_node_id?: string
}

// 任务列表响应 - 匹配后端API
export interface TaskListResponse {
  total: number
  page: number
  size: number
  items: Task[]
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
  id: string  // public_id
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

// 执行配置
export interface ExecutionConfig {
  max_instances: number
  timeout_seconds: number
  retry_count: number
  retry_delay: number
  execution_params?: Record<string, unknown>
  environment_vars?: Record<string, string>
}

// 任务列表查询参数
export interface TaskListParams {
  page?: number
  size?: number
  project_id?: string  // public_id
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
  node_id?: string     // 节点ID筛选
}

// 任务执行请求
export interface TaskExecuteRequest {
  task_id: string  // public_id
  execution_config?: Partial<ExecutionConfig>
  environment_variables?: Record<string, string>
}

// 分页响应
export interface PaginatedResponse<T> {
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
  success_rate: number
  average_duration: number
}

// 批量操作请求
export interface TaskBatchRequest {
  task_ids: string[]  // public_ids
  action: 'start' | 'stop' | 'cancel' | 'delete' | 'enable' | 'disable'
  execution_config?: Partial<ExecutionConfig>
}
