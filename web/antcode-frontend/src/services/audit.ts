/**
 * 审计日志服务
 */
import api from './api'

// 审计日志项
export interface AuditLogItem {
  id?: number | string
  action: string
  resource_type: string
  resource_id?: string
  resource_name?: string
  user_id?: number
  username: string
  ip_address?: string
  description?: string
  old_value?: Record<string, unknown> | null
  new_value?: Record<string, unknown> | null
  success: boolean
  error_message?: string
  created_at: string
}

// 审计日志列表响应
export interface AuditLogListResponse {
  total: number
  page: number
  page_size: number
  items: AuditLogItem[]
}

// 查询参数
export interface AuditLogQueryParams {
  page?: number
  page_size?: number
  action?: string
  resource_type?: string
  username?: string
  user_id?: number
  start_date?: string
  end_date?: string
  success?: boolean
}

// 审计统计
export interface AuditStats {
  total: number
  failed_count: number
  success_rate: number
  by_action: Record<string, number>
  by_user: Record<string, number>
  by_resource: Record<string, number>
  days: number
}

// 用户活动
export interface UserActivity {
  action: string
  resource_type: string
  resource_name?: string
  description?: string
  success: boolean
  created_at: string
}

// 操作类型选项
export interface ActionOption {
  value: string
  label: string
}

/**
 * 获取审计日志列表
 */
export async function getAuditLogs(params?: AuditLogQueryParams): Promise<AuditLogListResponse> {
  const response = await api.get('/api/v1/audit/logs', { params })
  return response.data.data
}

/**
 * 获取审计统计
 */
export async function getAuditStats(days: number = 7): Promise<AuditStats> {
  const response = await api.get('/api/v1/audit/stats', { params: { days } })
  return response.data.data
}

/**
 * 获取用户活动
 */
export async function getUserActivity(
  username: string,
  days: number = 30,
  limit: number = 100
): Promise<{ username: string; activity: UserActivity[] }> {
  const response = await api.get(`/api/v1/audit/user/${username}`, {
    params: { days, limit }
  })
  return response.data.data
}

/**
 * 清理旧日志
 */
export async function cleanupAuditLogs(days: number = 90): Promise<{ deleted: number }> {
  const response = await api.delete('/api/v1/audit/cleanup', { params: { days } })
  return response.data.data
}

/**
 * 获取操作类型列表
 */
export async function getAuditActions(): Promise<ActionOption[]> {
  const response = await api.get('/api/v1/audit/actions')
  return response.data.data
}

export default {
  getAuditLogs,
  getAuditStats,
  getUserActivity,
  cleanupAuditLogs,
  getAuditActions
}
