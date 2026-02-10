/**
 * 任务重试服务
 */
import api from './api'

// 重试统计
export interface RetryStats {
  task_id: string
  total_executions: number
  retried_executions: number
  total_retries: number
  retry_success_count: number
  retry_success_rate: number
  avg_retries_per_execution: number
}

// 待重试任务
export interface PendingRetry {
  task_id: string
  execution_id: string
  retry_time: string
  retry_count: number
}

// 重试历史项
export interface RetryHistoryItem {
  execution_id: string
  public_id: string
  status: string
  retry_count: number
  start_time?: string
  end_time?: string
  error_message?: string
}

// 重试配置
export interface RetryConfig {
  max_retries: number
  retry_delay: number
  strategy: string
}

/**
 * 手动重试任务
 */
export async function manualRetry(executionId: string): Promise<{
  success: boolean
  message: string
  execution_id: string
  retry_count: number
}> {
  const response = await api.post(`/api/v1/retry/manual/${executionId}`)
  return response.data.data
}

/**
 * 获取任务重试统计
 */
export async function getRetryStats(taskId: string): Promise<RetryStats> {
  const response = await api.get(`/api/v1/retry/stats/${taskId}`)
  return response.data.data
}

/**
 * 获取待重试任务列表
 */
export async function getPendingRetries(): Promise<{
  items: PendingRetry[]
  total: number
}> {
  const response = await api.get('/api/v1/retry/pending')
  return response.data.data
}

/**
 * 更新任务重试配置
 */
export async function updateRetryConfig(
  taskId: string,
  config: RetryConfig
): Promise<RetryConfig & { task_id: string }> {
  const response = await api.post(`/api/v1/retry/config/${taskId}`, config)
  return response.data.data
}

/**
 * 取消待重试任务
 */
export async function cancelPendingRetry(executionId: string): Promise<{
  execution_id: string
  status: string
}> {
  const response = await api.post(`/api/v1/retry/cancel/${executionId}`)
  return response.data.data
}

/**
 * 获取任务重试历史
 */
export async function getRetryHistory(
  taskId: string,
  page: number = 1,
  size: number = 20
): Promise<{
  items: RetryHistoryItem[]
  total: number
  page: number
  size: number
}> {
  const response = await api.get(`/api/v1/retry/history/${taskId}`, {
    params: { page, size }
  })
  return response.data.data
}

export default {
  manualRetry,
  getRetryStats,
  getPendingRetries,
  updateRetryConfig,
  cancelPendingRetry,
  getRetryHistory
}
