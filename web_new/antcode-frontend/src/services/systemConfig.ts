/**
 * 系统配置管理服务
 */
import apiClient from './api'
import type {
  SystemConfig,
  CreateSystemConfigRequest,
  UpdateSystemConfigRequest,
  BatchUpdateConfigRequest,
  AllSystemConfigs,
} from '@/types/system-config'
import type { ApiResponse } from '@/types/api'

/**
 * 获取所有系统配置
 */
export const getAllConfigs = async (category?: string): Promise<ApiResponse<SystemConfig[]>> => {
  const params = category ? { category } : {}
  const response = await apiClient.get('/api/v1/system-config/', { params })
  return response.data
}

/**
 * 按分类获取系统配置
 */
export const getConfigsByCategory = async (): Promise<ApiResponse<AllSystemConfigs>> => {
  const response = await apiClient.get('/api/v1/system-config/by-category')
  return response.data
}

/**
 * 获取单个系统配置
 */
export const getConfig = async (configKey: string): Promise<ApiResponse<SystemConfig>> => {
  const response = await apiClient.get(`/api/v1/system-config/${configKey}`)
  return response.data
}

/**
 * 创建系统配置
 */
export const createConfig = async (
  data: CreateSystemConfigRequest
): Promise<ApiResponse<SystemConfig>> => {
  const response = await apiClient.post('/api/v1/system-config/', data)
  return response.data
}

/**
 * 更新系统配置
 */
export const updateConfig = async (
  configKey: string,
  data: UpdateSystemConfigRequest
): Promise<ApiResponse<SystemConfig>> => {
  const response = await apiClient.put(`/api/v1/system-config/${configKey}`, data)
  return response.data
}

/**
 * 批量更新系统配置
 */
export const batchUpdateConfigs = async (
  data: BatchUpdateConfigRequest
): Promise<ApiResponse<{ updated_count: number }>> => {
  const response = await apiClient.post('/api/v1/system-config/batch', data)
  return response.data
}

/**
 * 删除系统配置
 */
export const deleteConfig = async (configKey: string): Promise<ApiResponse<null>> => {
  const response = await apiClient.delete(`/api/v1/system-config/${configKey}`)
  return response.data
}

/**
 * 重新加载配置（热加载）
 */
export const reloadConfigs = async (): Promise<ApiResponse<null>> => {
  const response = await apiClient.post('/api/v1/system-config/reload')
  return response.data
}

/**
 * 初始化默认配置
 */
export const initializeDefaultConfigs = async (): Promise<ApiResponse<null>> => {
  const response = await apiClient.post('/api/v1/system-config/initialize')
  return response.data
}

export const systemConfigService = {
  getAllConfigs,
  getConfigsByCategory,
  getConfig,
  createConfig,
  updateConfig,
  batchUpdateConfigs,
  deleteConfig,
  reloadConfigs,
  initializeDefaultConfigs,
}

export default systemConfigService

