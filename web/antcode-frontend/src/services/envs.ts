import apiClient from './api'
import type { ApiResponse } from '@/types'

export interface PythonVersionsResponse {
  versions: string[]
}

export type VenvScope = 'shared' | 'private'

export interface CreateOrBindVenvRequest {
  version: string
  venv_scope: VenvScope
  shared_venv_key?: string
  create_if_missing?: boolean
  interpreter_source?: string
  python_bin?: string
}

export interface VenvListItem {
  id: string  // public_id
  scope: VenvScope
  key?: string | null
  version: string
  venv_path: string
  interpreter_version: string
  interpreter_source?: string
  python_bin: string
  install_dir: string
  created_by?: string | null  // public_id
  created_by_username?: string | null
  created_at?: string | null
  updated_at?: string | null
  current_project_id?: string | null
  packages?: Array<{ name: string; version: string }>
}

export interface PaginatedVenvs {
  items: VenvListItem[]
  page: number
  size: number
  total: number
  pages: number
}

class EnvService {
  async listPythonVersions(): Promise<string[]> {
    const res = await apiClient.get<PythonVersionsResponse>('/api/v1/envs/python/versions')
    return res.data.versions || []
  }

  async listInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    const res = await apiClient.get<Array<{ version: string; python_bin: string; install_dir: string; source?: string; id?: number }>>('/api/v1/envs/python/interpreters')
    return res.data || []
  }

  // 获取已安装的解释器列表（用于项目创建时选择）
  async listInstalledInterpreters(): Promise<Array<{ version: string; python_bin: string; install_dir: string; source?: string }>> {
    return this.listInterpreters()
  }

  async installInterpreter(version: string): Promise<void> {
    await apiClient.post('/api/v1/envs/python/interpreters', { version })
  }

  async registerLocalInterpreter(python_bin: string): Promise<void> {
    await apiClient.post('/api/v1/envs/python/interpreters/local', { python_bin })
  }

  async uninstallInterpreter(version: string, source: string = 'mise'): Promise<void> {
    await apiClient.delete(`/api/v1/envs/python/interpreters/${encodeURIComponent(version)}`, { params: { source } })
  }

  // ========== Worker 环境管理 API ==========

  async listWorkerEnvs(workerId: string): Promise<Array<{ name: string; python_version: string }>> {
    const res = await apiClient.get<ApiResponse<Array<{ name: string; python_version: string }>>>(
      `/api/v1/workers/${workerId}/envs`
    )
    return res.data.data || []
  }

  async listWorkerInterpreters(workerId: string): Promise<{ interpreters: Array<{ version: string; source?: string }>; total: number }> {
    const res = await apiClient.get<ApiResponse<Array<{ version: string; source?: string }>>>(
      `/api/v1/workers/${workerId}/interpreters`
    )
    const interpreters = res.data.data || []
    return { interpreters, total: interpreters.length }
  }

  async listVenvs(params: {
    scope?: VenvScope
    project_id?: string
    q?: string
    page?: number
    size?: number
    include_packages?: boolean
    limit_packages?: number
    interpreter_source?: string
    worker_id?: string  // Worker ID 筛选
  }): Promise<PaginatedVenvs> {
    const res = await apiClient.get<{
      success: boolean
      data: VenvListItem[]
      pagination: { page: number; size: number; total: number; pages: number }
    }>('/api/v1/envs/venvs', { params })
    return {
      items: res.data.data || [],
      page: res.data.pagination?.page || 1,
      size: res.data.pagination?.size || 20,
      total: res.data.pagination?.total || 0,
      pages: res.data.pagination?.pages || 1,
    }
  }

  async batchDeleteVenvs(ids: string[]): Promise<{ total: number; deleted: number; failed: string[] }> {
    const res = await apiClient.post<{ total: number; deleted: number; failed: string[] }>('/api/v1/envs/venvs/batch-delete', { ids })
    return res.data
  }

  async listVenvPackagesById(venv_id: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ venv_id: string; packages: Array<{ name: string; version: string }> }>(`/api/v1/envs/venvs/${venv_id}/packages`)
    return res.data.packages || []
  }

  async listProjectVenvPackages(project_id: string): Promise<Array<{ name: string; version: string }>> {
    const res = await apiClient.get<{ project_id: string; packages: Array<{ name: string; version: string }> }>(`/api/v1/envs/projects/${project_id}/venv/packages`)
    return res.data.packages || []
  }

  async createOrBindProjectVenv(project_id: string, payload: CreateOrBindVenvRequest): Promise<void> {
    await apiClient.post(`/api/v1/envs/projects/${project_id}/venv`, payload)
  }

  async deleteProjectVenv(project_id: string): Promise<void> {
    await apiClient.delete(`/api/v1/envs/projects/${project_id}/venv`)
  }

  async createSharedVenv(payload: { version: string; shared_venv_key?: string; interpreter_source?: string; python_bin?: string }): Promise<Record<string, unknown>> {
    const res = await apiClient.post<Record<string, unknown>>('/api/v1/envs/venvs', payload)
    return res.data
  }

  async deleteVenv(venv_id: string, allowPrivate = false): Promise<void> {
    await apiClient.delete(`/api/v1/envs/venvs/${venv_id}`, { params: { allow_private: allowPrivate } })
  }

  async updateSharedVenv(venv_id: string, payload: { key?: string }): Promise<void> {
    await apiClient.patch(`/api/v1/envs/venvs/${venv_id}`, payload)
  }

  async installPackagesToVenv(venv_id: string, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/envs/venvs/${venv_id}/packages`, { packages })
  }

  async installPackagesToProjectVenv(project_id: string, packages: string[]): Promise<void> {
    await apiClient.post(`/api/v1/envs/projects/${project_id}/venv/packages`, { packages })
  }
}

const envService = new EnvService()
export default envService
