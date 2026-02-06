/**
 * 系统信息服务
 */
import axios from 'axios'
import { API_BASE_URL } from '@/utils/constants'

export interface HealthInfo {
  status: string
  version: string
  timestamp: string
}

export interface AppInfo {
  name: string  // 应用名称，用于侧边栏等
  title: string  // 完整标题，用于页脚等
  version: string
  description?: string
  copyright_year: string
}

// 独立的 axios 实例，避免触发全局错误通知
const systemApi = axios.create({
  baseURL: API_BASE_URL,
  timeout: 5000,
})

/**
 * 获取系统健康信息
 */
export const getHealthInfo = async (): Promise<HealthInfo> => {
  const response = await systemApi.get<{ data: HealthInfo }>('/api/v1/health')
  return response.data.data
}

/**
 * 获取应用信息（名称、版本、标题等）
 */
export const getAppInfo = async (): Promise<AppInfo> => {
  const response = await systemApi.get<{ data: AppInfo }>('/api/v1/app-info')
  return response.data.data
}
