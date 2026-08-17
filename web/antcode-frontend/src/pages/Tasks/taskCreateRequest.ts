import type { TaskCreateRequest } from '@/types'

/** 表单里这三个字段是字符串/控件对象，和提交给后端的结构不同。 */
export interface TaskCreateFormValues
  extends Omit<TaskCreateRequest, 'execution_params' | 'environment_vars' | 'scheduled_time'> {
  execution_params?: string
  environment_vars?: string
  scheduled_time?: { toISOString: () => string }
}

const DEFAULT_MAX_INSTANCES = 1
const DEFAULT_TIMEOUT_SECONDS = 3600
const DEFAULT_RETRY_COUNT = 3
const DEFAULT_RETRY_DELAY_SECONDS = 60

/**
 * 把创建表单的值转成后端要的 TaskCreateRequest。
 *
 * execution_params / environment_vars 在表单层已由 validateJSON 校验过格式，
 * 这里直接 JSON.parse；只有 execution_strategy 为 specified 时才带 Worker 指派。
 */
export const buildTaskCreateRequest = (values: TaskCreateFormValues): TaskCreateRequest => {
  const executionStrategy = values.execution_strategy || undefined
  return {
    name: values.name,
    description: values.description,
    project_id: values.project_id,
    schedule_type: values.schedule_type,
    cron_expression: values.cron_expression,
    interval_seconds: values.interval_seconds,
    scheduled_time: values.scheduled_time?.toISOString(),
    max_instances: values.max_instances || DEFAULT_MAX_INSTANCES,
    timeout_seconds: values.timeout_seconds || DEFAULT_TIMEOUT_SECONDS,
    retry_count: values.retry_count || DEFAULT_RETRY_COUNT,
    retry_delay: values.retry_delay || DEFAULT_RETRY_DELAY_SECONDS,
    execution_params: values.execution_params ? JSON.parse(values.execution_params) : undefined,
    environment_vars: values.environment_vars ? JSON.parse(values.environment_vars) : undefined,
    is_active: values.is_active !== false,
    execution_strategy: executionStrategy,
    specified_worker_id:
      executionStrategy === 'specified' ? values.specified_worker_id : undefined,
  }
}
